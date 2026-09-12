"""B13b (audit L-03): акты в ОРД — предпросмотр, черновик, отправка, коррекция.

* Акт 15.09–15.10 по договору → строки по (креатив, версия erid, месяц):
  сентябрьский и октябрьский срезы с датами «месяц ∩ период акта».
* Расход строк — цена клиента (net × spend_multiplier владельца договора).
* (креатив, версия, месяц) входит только в один акт → второй акт 409.
* submit: invoice → link → statistic; статусы ok/partial/failed, построчно.
* PUT после отправки → draft, повторный submit — коррекция (тот же externalInvoiceId).
* Месячная выгрузка крона по принятой строке акта берёт значения акта.
* B13e (ОРС-модель): акт по исполнительскому договору собирает показы кампаний
  с этим договором; строки и сумма делятся по изначальным договорам
  (contracts[] в v2/invoice, link по изначальному договору); НДС 22 %:
  amount_gross/amount_net; расхождение с фактом > 1 % — предупреждение.
Требует DATABASE_URL, ОРД — mock. Спека: docs/audit/ord_spec.md §6–7.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg
import httpx
from fastapi import FastAPI

import db
import routers.ord_router as ordr
from auth import current_user
from ord.service import register_creative
from routers import cron as cron_router
from tests.test_ord_flow_db import _admin, _Env


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    db._pool = None
    yield
    db._pool = None


def _app(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(ordr.router)
    app.include_router(cron_router.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _add_stats(conn, e, ts_hour, imps, clicks=1, spend="1.0", creative=None):
    await conn.execute(
        """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, bids, wins,
                                     impressions, clicks, spend)
           VALUES ($1, $2, $3, $4, $5, $5, $5, $6, $7::numeric)""",
        ts_hour, e.ids["camp"], e.ids["li"], creative or e.ids["cr"], imps, clicks, spend,
    )


async def _setup(conn):
    """Договор + креатив с erid (mock) + показы 15.09 и 05.10 (акт на стыке месяцев).
    Показы датированы 2025 годом, чтобы не пересекаться с другими тестами."""
    e = await _Env().setup(conn, geo=["RU"])
    ct = await e.add_contract(conn)
    cr = await e.add_creative(conn, status="approved", contract_id=ct)
    e.ids.update(ct=ct, cr=cr)
    pool = await db.get_pool()
    res = await register_creative(pool, dict(await conn.fetchrow("SELECT * FROM creatives WHERE id = $1", cr)))
    assert res["outcome"] == "ok"
    e.ids["erid"] = res["erid"]
    # registered_at должен быть раньше показов: сдвигаем регистрацию в прошлое
    await conn.execute("UPDATE ord_creatives SET registered_at = '2025-01-01', created_at = '2025-01-01' WHERE creative_id = $1", cr)
    await _add_stats(conn, e, datetime(2025, 9, 15, 10, tzinfo=UTC), 1000, clicks=10, spend="20.00")
    await _add_stats(conn, e, datetime(2025, 9, 20, 10, tzinfo=UTC), 500, clicks=5, spend="10.00")
    await _add_stats(conn, e, datetime(2025, 10, 5, 10, tzinfo=UTC), 300, clicks=3, spend="9.00")
    await _add_stats(conn, e, datetime(2025, 10, 20, 10, tzinfo=UTC), 999, clicks=9, spend="99.00")  # вне периода
    return e


def _invoice_body(e, items, **over):
    body = {
        "contract_id": e.ids["ct"], "number": "А-1", "invoice_date": "2025-10-20",
        "period_start": "2025-09-15", "period_end": "2025-10-15", "items": items,
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
def test_preview_splits_by_month_and_prices_at_client_rate(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": e.ids["ct"], "period_start": "2025-09-15", "period_end": "2025-10-15",
                })
            assert r.status_code == 200, r.text
            p = r.json()
            assert p["months"] == ["2025-09", "2025-10"]
            assert p["client_role"] == "advertiser" and p["contractor_role"] == "ors"
            items = p["items"]
            assert [(i["period"], i["fact_impressions"], i["fact_clicks"], i["fact_spend"]) for i in items] == [
                ("2025-09", 1500, 15, "30.00"), ("2025-10", 300, 3, "9.00"),
            ]
            assert items[0]["period_start"] == "2025-09-15" and items[0]["period_end"] == "2025-09-30"
            assert items[1]["period_start"] == "2025-10-01" and items[1]["period_end"] == "2025-10-15"
            assert items[0]["erid"] == e.ids["erid"] and items[0]["version"] == 1
            assert items[0]["unit_cost"] == "20.0000"  # 30 / 1500 * 1000
            assert items[0]["external_statistic_id"] == f"{e.ids['cr']}:v1:2025-09"
            assert p["amount_gross"] == "39.00" and p["spend_multiplier"] == 1.0  # admin: без наценки
            assert p["vat_rate"] == 22 and p["amount_net"] == "31.97"  # 39 / 1.22
            assert p["statistics_amount_gross"] == "39.00"
            assert [(c["id"], c["amount_gross"], c["items_count"]) for c in p["contracts"]] == [(e.ids["ct"], "39.00", 2)]
            assert p["warnings"] == {"already_invoiced": [], "without_erid": [], "contracts_unregistered": []}

            # период за пределами показов — пусто, но не ошибка
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": e.ids["ct"], "period_start": "2025-11-01", "period_end": "2025-11-30",
                })
            assert r.status_code == 200 and r.json()["items"] == [] and r.json()["amount_gross"] == "0.00"
            # неверный период
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": e.ids["ct"], "period_start": "2025-10-15", "period_end": "2025-09-15",
                })
            assert r.status_code == 422
        finally:
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_preview_warns_about_impressions_without_erid(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            unmarked = await e.add_creative(conn, status="approved", contract_id=e.ids["ct"])
            await _add_stats(conn, e, datetime(2025, 9, 16, 10, tzinfo=UTC), 77, creative=unmarked)
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": e.ids["ct"], "period_start": "2025-09-01", "period_end": "2025-09-30",
                })
            assert r.status_code == 200
            w = r.json()["warnings"]["without_erid"]
            assert [(x["creative_id"], x["impressions"]) for x in w] == [(unmarked, 77)]
        finally:
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_create_submit_edit_resubmit_and_conflicts(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            items = [
                {"creative_id": e.ids["cr"], "version": 1, "period": "2025-09"},
                {"creative_id": e.ids["cr"], "version": 1, "period": "2025-10", "impressions": 280, "spend": "8.40"},
            ]
            async with _app(_admin(e)) as c:
                r = await c.post("/ord/invoices", json=_invoice_body(e, items))
            assert r.status_code == 201, r.text
            inv = r.json()
            inv_id = inv["id"]
            assert inv["status"] == "draft" and Decimal(str(inv["amount_gross"])) == Decimal("38.40")  # 30.00 + 8.40
            assert inv["vat_rate"] == 22 and Decimal(str(inv["amount_net"])) == Decimal("31.48")
            assert [(c["initial_contract_id"], Decimal(str(c["amount_gross"]))) for c in inv["contracts"]] == [
                (e.ids["ct"], Decimal("38.40"))]
            assert Decimal(str(inv["statistics_amount_gross"])) == Decimal("39.00")
            assert inv["amount_diff_pct"] == 1.54 and inv["warnings"]["amount_diff"]["pct"] == 1.54
            got = {i["period"]: i for i in inv["items"]}
            assert got["2025-09"]["impressions"] == 1500 and got["2025-09"]["fact_impressions"] == 1500
            assert got["2025-10"]["impressions"] == 280 and got["2025-10"]["fact_impressions"] == 300
            assert Decimal(str(got["2025-10"]["spend"])) == Decimal("8.40") and Decimal(str(got["2025-10"]["fact_spend"])) == Decimal("9.00")
            assert Decimal(str(got["2025-10"]["unit_cost"])) == Decimal("30.0000")  # 8.40 / 280 * 1000
            assert all(i["status"] == "pending" for i in inv["items"])

            # второй акт с той же строкой (креатив, версия, месяц) — 409
            async with _app(_admin(e)) as c:
                r = await c.post("/ord/invoices", json=_invoice_body(e, items[:1], number="А-2"))
            assert r.status_code == 409 and "А-1" in r.json()["detail"]
            # тот же номер по договору — 409
            async with _app(_admin(e)) as c:
                r = await c.post("/ord/invoices", json=_invoice_body(
                    e, [{"creative_id": e.ids["cr"], "version": 1, "period": "2025-11"}],
                    period_start="2025-11-01", period_end="2025-11-30"))
            assert r.status_code == 409 and "уже существует" in r.json()["detail"]
            # месяц строки вне периода акта — 422; чужая версия — 404
            async with _app(_admin(e)) as c:
                r = await c.post("/ord/invoices", json=_invoice_body(
                    e, [{"creative_id": e.ids["cr"], "version": 1, "period": "2025-12"}], number="А-3"))
                assert r.status_code == 422, r.text
                r = await c.post("/ord/invoices", json=_invoice_body(
                    e, [{"creative_id": e.ids["cr"], "version": 9, "period": "2025-09"}], number="А-3"))
                assert r.status_code == 404, r.text

            # предпросмотр помечает занятые строки; при редактировании этого же акта — нет
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": e.ids["ct"], "period_start": "2025-09-15", "period_end": "2025-10-15"})
                assert [i["in_invoice"]["number"] for i in r.json()["items"]] == ["А-1", "А-1"]
                assert r.json()["amount_gross"] == "0.00" and r.json()["contracts"] == []
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": e.ids["ct"], "period_start": "2025-09-15", "period_end": "2025-10-15",
                    "invoice_id": inv_id})
                assert all(i["in_invoice"] is None for i in r.json()["items"])

            # submit: mock принимает всё
            async with _app(_admin(e)) as c:
                r = await c.post(f"/ord/invoices/{inv_id}/submit")
            assert r.status_code == 200, r.text
            sub = r.json()
            assert sub["status"] == "ok" and sub["ord_external_id"] == inv_id and sub["ord_provider"] == "mock"
            assert {i["status"] for i in sub["items"]} == {"accepted"}
            assert set(sub["ord_response"]) == {"2025-09", "2025-10"}

            # отправленный акт удалить нельзя
            async with _app(_admin(e)) as c:
                r = await c.delete(f"/ord/invoices/{inv_id}")
            assert r.status_code == 409

            # месячная выгрузка за 2025-10 берёт значения принятого акта, факт — из БД
            async with _app(_admin(e)) as c:
                r = await c.get("/cron/ord-export", params={"period": "2025-10", "force": "true"})
            assert r.status_code == 200, r.text
            req = await conn.fetchval("SELECT request FROM ord_export_log WHERE id = $1", r.json()["log_id"])
            row = next(s for s in json.loads(req)["stats"] if s["creative_id"] == e.ids["cr"])
            assert row["impressions"] == 300 + 999 and row["invoice_impressions"] == 280
            assert row["invoice_spend"] == "8.40" and row["date_start"] == "2025-10-01" and row["date_end"] == "2025-10-15"

            # редактирование после отправки → draft; повторный submit — коррекция, тот же externalInvoiceId
            items2 = [{"creative_id": e.ids["cr"], "version": 1, "period": "2025-09", "impressions": 1400, "spend": "28.00"}]
            async with _app(_admin(e)) as c:
                r = await c.put(f"/ord/invoices/{inv_id}", json=_invoice_body(e, items2, amount_gross="28.00", is_vat=False))
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "draft" and len(r.json()["items"]) == 1
            assert r.json()["vat_rate"] == 0 and Decimal(str(r.json()["amount_net"])) == Decimal("28.00")
            assert r.json()["warnings"]["amount_diff"]["pct"] == 6.67  # 28.00 против факта 30.00
            assert r.json()["ord_external_id"] == inv_id
            async with _app(_admin(e)) as c:
                r = await c.post(f"/ord/invoices/{inv_id}/submit")
            assert r.status_code == 200 and r.json()["status"] == "ok"
            # смена договора акта запрещена
            other_ct = await e.add_contract(conn, number="Д-2")
            async with _app(_admin(e)) as c:
                r = await c.put(f"/ord/invoices/{inv_id}", json=_invoice_body(e, items2, contract_id=other_ct))
            assert r.status_code == 409

            # список и карточка
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices")
                lst = [x for x in r.json() if x["id"] == inv_id]
                assert lst and lst[0]["items_count"] == 1 and lst[0]["items_accepted"] == 1
                r = await c.get(f"/ord/invoices/{inv_id}")
                assert r.status_code == 200 and r.json()["contract_number"] == "Д-1"
        finally:
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_submit_partial_and_failed(env, monkeypatch):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            # строка с нулём показов — mock отклоняет её как реальный ОРД → partial
            items = [
                {"creative_id": e.ids["cr"], "version": 1, "period": "2025-09"},
                {"creative_id": e.ids["cr"], "version": 1, "period": "2025-10", "impressions": 0, "spend": "0"},
            ]
            async with _app(_admin(e)) as c:
                r = await c.post("/ord/invoices", json=_invoice_body(e, items))
                assert r.status_code == 201, r.text
                inv_id = r.json()["id"]
                r = await c.post(f"/ord/invoices/{inv_id}/submit")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "partial"
            st = {i["period"]: (i["status"], i["error"]) for i in r.json()["items"]}
            assert st["2025-09"] == ("accepted", None)
            assert st["2025-10"][0] == "rejected" and "positive" in st["2025-10"][1]

            # ОРД недоступен на регистрации акта → failed, 502, все строки rejected
            import ord.mock as mock_mod
            from ord.adapter import OrdError

            async def boom(self, data):
                raise OrdError("ОРД Ozon 5xx: 503")

            monkeypatch.setattr(mock_mod.MockOrdAdapter, "register_invoice", boom)
            async with _app(_admin(e)) as c:
                r = await c.post(f"/ord/invoices/{inv_id}/submit")
            assert r.status_code == 502
            row = await conn.fetchrow("SELECT status, error FROM ord_invoices WHERE id = $1", inv_id)
            assert row["status"] == "failed" and "5xx" in row["error"]
            sts = await conn.fetch("SELECT status FROM ord_invoice_items WHERE invoice_id = $1", inv_id)
            assert {r["status"] for r in sts} == {"rejected"}
        finally:
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_invoice_scope_is_owner_bound(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        stranger = await _Env().setup(conn, geo=["RU"])
        try:
            async with _app(_admin(e)) as c:
                r = await c.post("/ord/invoices", json=_invoice_body(
                    e, [{"creative_id": e.ids["cr"], "version": 1, "period": "2025-09"}]))
                assert r.status_code == 201, r.text
                inv_id = r.json()["id"]
            # чужой advertiser (не платформенная роль) акт не видит и не может отправить/удалить
            outsider = {**_admin(stranger), "role": "advertiser"}
            async with _app(outsider) as c:
                assert (await c.get(f"/ord/invoices/{inv_id}")).status_code == 404
                assert inv_id not in {x["id"] for x in (await c.get("/ord/invoices")).json()}
                assert (await c.post(f"/ord/invoices/{inv_id}/submit")).status_code in (403, 404)
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": e.ids["ct"], "period_start": "2025-09-01", "period_end": "2025-09-30"})
                assert r.status_code == 404
        finally:
            await e.teardown(conn)
            await stranger.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_ors_invoice_by_executive_contract_splits_by_initial_contracts(env, monkeypatch):
    """ОРС-модель: акт по исполнительскому договору (агентство → ОРС) собирает
    показы двух кампаний с разными изначальными договорами; в ОРД уходит
    contracts[] по изначальным договорам и link по каждому из них."""
    async def run():
        from tests.test_ord_chain_db import _chain_env, _teardown

        conn = await asyncpg.connect(DATABASE_URL)
        e, ids = await _chain_env(conn)
        try:
            pool = await db.get_pool()
            # кампания 1: initial И-1 + executive Р-1 (из _chain_env)
            await conn.execute(
                """UPDATE campaigns SET status='active', ord_mode='own_client', counterparty_id=$2,
                          initial_contract_id=$3, executive_contract_id=$4, ord_confirmed_at=now() WHERE id=$1""",
                e.ids["camp"], e.ids["cp"], ids["initial"], ids["executive"],
            )
            cr1 = await e.add_creative(conn, status="approved", contract_id=ids["initial"])
            # кампания 2: другой изначальный договор И-2 с тем же исполнительским
            initial2 = await conn.fetchval(
                """INSERT INTO ord_contracts (user_id, client_id, contractor_id, number, signed_at, ord_status, ord_external_id)
                   VALUES ($1,$2,$3,'И-2','2026-01-01','registered','CT-I2') RETURNING id""",
                e.ids["user"], e.ids["cp"], ids["agency"],
            )
            camp2 = await conn.fetchval(
                """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget,
                                          ord_mode, counterparty_id, initial_contract_id, executive_contract_id, ord_confirmed_at)
                   VALUES ($1,$2,'camp2','active',100,0,'own_client',$3,$4,$5,now()) RETURNING id""",
                e.ids["adv"], e.ids["user"], e.ids["cp"], initial2, ids["executive"],
            )
            cr2 = await conn.fetchval(
                """INSERT INTO creatives (campaign_id, user_id, name, type, width, height, asset_url, click_url,
                                          status, counterparty_id, contract_id, kktu_code)
                   VALUES ($1,$2,'c2','banner',300,250,'http://a/2.png','http://x','approved',$3,$4,'30.10') RETURNING id""",
                camp2, e.ids["user"], e.ids["cp"], initial2,
            )
            for cr in (cr1, cr2):
                res = await register_creative(pool, dict(await conn.fetchrow("SELECT * FROM creatives WHERE id = $1", cr)))
                assert res["outcome"] == "ok", res
            await conn.execute("UPDATE ord_creatives SET registered_at='2025-01-01', created_at='2025-01-01' WHERE creative_id = ANY($1::uuid[])", [cr1, cr2])
            await _add_stats(conn, e, datetime(2025, 9, 10, 10, tzinfo=UTC), 1000, spend="30.00", creative=cr1)
            await conn.execute(
                """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, bids, wins, impressions, clicks, spend)
                   VALUES ($1, $2, NULL, $3, 500, 500, 500, 2, 10.00)""",
                datetime(2025, 9, 12, 10, tzinfo=UTC), camp2, cr2,
            )

            # предпросмотр по исполнительскому договору: две строки, два изначальных договора
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": ids["executive"], "period_start": "2025-09-01", "period_end": "2025-09-30"})
            assert r.status_code == 200, r.text
            p = r.json()
            assert p["client_role"] == "agency" and p["contractor_role"] == "ors"
            assert {(i["creative_id"], i["contract_id"]) for i in p["items"]} == {(str(cr1), ids["initial"]), (str(cr2), str(initial2))}
            assert {(c["number"], c["amount_gross"]) for c in p["contracts"]} == {("И-1", "30.00"), ("И-2", "10.00")}
            assert "contract_not_executive" not in p["warnings"]
            # по изначальному договору акт заключать не следует — предупреждение
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": ids["initial"], "period_start": "2025-09-01", "period_end": "2025-09-30"})
            assert r.status_code == 200 and r.json()["warnings"]["contract_not_executive"] is True

            # явная разбивка должна сходиться с суммой акта
            items = [{"creative_id": str(cr1), "version": 1, "period": "2025-09"},
                     {"creative_id": str(cr2), "version": 1, "period": "2025-09"}]
            body = {"contract_id": ids["executive"], "number": "ОРС-1", "invoice_date": "2025-10-05",
                    "period_start": "2025-09-01", "period_end": "2025-09-30", "items": items}
            async with _app(_admin(e)) as c:
                r = await c.post("/ord/invoices", json={**body, "amount_gross": 50, "contracts": [
                    {"initial_contract_id": ids["initial"], "amount_gross": 30}]})
                assert r.status_code == 422, r.text
                r = await c.post("/ord/invoices", json={**body, "amount_gross": 50, "contracts": [
                    {"initial_contract_id": ids["initial"], "amount_gross": 35},
                    {"initial_contract_id": str(initial2), "amount_gross": 15}]})
                assert r.status_code == 201, r.text
            inv = r.json()
            assert {(c["number"], Decimal(str(c["amount_gross"])), Decimal(str(c["amount_net"]))) for c in inv["contracts"]} == {
                ("И-1", Decimal("35.00"), Decimal("28.69")), ("И-2", Decimal("15.00"), Decimal("12.30"))}
            assert Decimal(str(inv["amount_net"])) == Decimal("40.98")
            assert inv["warnings"]["amount_diff"]["pct"] == 25.0  # 50 против факта 40

            # без явной разбивки — пропорционально расходу строк, копейка в копейку
            async with _app(_admin(e)) as c:
                r = await c.put(f"/ord/invoices/{inv['id']}", json={**body, "amount_gross": "40.01"})
            assert r.status_code == 200, r.text
            split = {c["number"]: Decimal(str(c["amount_gross"])) for c in r.json()["contracts"]}
            assert sum(split.values()) == Decimal("40.01") and split["И-1"] == Decimal("30.01")

            # submit: в ОРД уходят contracts[] по изначальным договорам и link по каждому
            import ord.mock as mock_mod
            sent: dict = {}

            async def spy_invoice(self, data):
                sent["invoice"] = data
                return {"external_id": data["external_id"]}

            async def spy_link(self, data):
                sent["link"] = data

            monkeypatch.setattr(mock_mod.MockOrdAdapter, "register_invoice", spy_invoice)
            monkeypatch.setattr(mock_mod.MockOrdAdapter, "link_invoice_creatives", spy_link)
            async with _app(_admin(e)) as c:
                r = await c.post(f"/ord/invoices/{inv['id']}/submit")
            assert r.status_code == 200 and r.json()["status"] == "ok", r.text
            assert sent["invoice"]["contract_external_id"] == "CT-E1"
            assert sorted((c["contract_external_id"], str(c["amount_gross"])) for c in sent["invoice"]["contracts"]) == [
                ("CT-I1", "30.01"), ("CT-I2", "10.00")]
            assert sorted((g["contract_external_id"], g["external_creative_ids"]) for g in sent["link"]["groups"]) == [
                ("CT-I1", [f"{cr1}:v1"]), ("CT-I2", [f"{cr2}:v1"])]

            # изначальный договор без регистрации в ОРД → submit 409
            await conn.execute("UPDATE ord_contracts SET ord_status='pending', ord_external_id=NULL WHERE id = $1", initial2)
            async with _app(_admin(e)) as c:
                r = await c.post(f"/ord/invoices/{inv['id']}/submit")
            assert r.status_code == 409 and "И-2" in r.json()["detail"]
        finally:
            await conn.execute("DELETE FROM ord_invoices WHERE user_id = $1", e.ids["user"])
            await _teardown(e, conn)
            await conn.close()

    asyncio.run(run())
