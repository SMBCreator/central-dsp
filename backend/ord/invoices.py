"""Акты (разаллокация) в ОРД — B13b, audit L-03. Спека: docs/audit/ord_spec.md §6.

Модель:
  * акт = один договор + произвольный период (например 15.09–15.10) + сумма;
  * строки акта = статистика по (креатив, версия erid, календарный месяц):
    ОРД принимает статистику строго в рамках месяца, поэтому акт на стыке
    месяцев даёт по две строки на креатив с датами «месяц ∩ период акта»;
  * externalStatisticId строки = <creative>:v<n>:<YYYY-MM> — тот же, что у
    месячной выгрузки крона: отправка акта = коррекция той же строки в ОРД
    (viewsCountByFact = факт, viewsCountByInvoice/moneySpent = значения акта),
    двойного учёта нет. Следствие: (креатив, версия, месяц) — только в одном акте;
  * расход в строках и сумма акта — цена клиента: net × rbac.spend_multiplier
    владельца договора (в БД net не меняется; та же точка, что в отчётах);
  * повторная отправка = upsert акта + пересборка связей + upsert строк.

ОРС-модель (B13e, ord_spec §7): акт заключается по **исполнительскому** договору
(заказчик — агентство или сам рекламодатель, исполнитель — наша организация,
ОРС). В акт попадают показы кампаний, у которых этот договор — исполнительский
(``campaigns.executive_contract_id``), а также креативы, зарегистрированные
прямо на него (режим ``self`` и акты до B13d). Строки группируются по
**изначальному** договору кампании (``ord_invoice_items.contract_id``): по нему
креатив привязывается к акту в ОРД, а сумма акта разбивается по этим договорам
(``ord_invoice_contracts`` → ``contracts[]`` в v2/invoice). Суммы хранятся с НДС
(``amount_gross``, как в первичке) и без (``amount_net``) по ставке ``vat_rate``.
Расхождение суммы акта с фактом статистики > 1 % — предупреждение, не блок.

Все функции получают pool и уже проверенные по scope строки; HTTP-семантика
(404/409/422) — через InvoiceError(status, detail), маппится в роутере.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ad_market import foreign_campaign_sql

from .adapter import OrdError, get_ord_adapter
from .platform import ensure_platform
from .service import external_creative_id, external_statistic_id

ROLES = ("advertiser", "agency", "publisher", "ors")
PAYMENT_TYPES = ("cpm", "cpc")
MAX_PERIOD_DAYS = 366
DEFAULT_VAT_RATE = 22          # НДС в РФ с 01.01.2026
AMOUNT_DIFF_WARN_PCT = 1.0     # акт ≠ факт больше чем на 1 % → предупреждение


class InvoiceError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ------------------------------------------------------------------ helpers --
def _q2(x: Any) -> Decimal:
    return Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q4(x: Any) -> Decimal:
    return Decimal(str(x or 0)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def month_slices(start: date, end: date) -> list[tuple[str, date, date]]:
    """Период акта → [(YYYY-MM, начало среза, конец среза)] по календарным месяцам."""
    if end < start:
        raise InvoiceError(422, "period_end раньше period_start")
    out: list[tuple[str, date, date]] = []
    cur = start
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        out.append((f"{cur.year:04d}-{cur.month:02d}", cur, min(last, end)))
        cur = last + timedelta(days=1)
    return out


def unit_cost(payment_type: str, impressions: int, clicks: int, spend: Any) -> Decimal:
    """CPM — за 1000 показов, CPC — за клик; без знаменателя — 0."""
    s = Decimal(str(spend or 0))
    if payment_type == "cpc":
        return _q4(s / clicks) if clicks else Decimal("0.0000")
    return _q4(s / impressions * 1000) if impressions else Decimal("0.0000")


def net_of(gross: Any, is_vat: bool, vat_rate: int) -> Decimal:
    """Сумма без НДС из суммы с НДС; без НДС — та же сумма."""
    g = _q2(gross)
    if not is_vat or not vat_rate:
        return g
    return _q2(g / (Decimal(1) + Decimal(vat_rate) / Decimal(100)))


def parse_vat_rate(v: Any, is_vat: bool) -> int:
    if not is_vat:
        return 0
    if v in (None, ""):
        return DEFAULT_VAT_RATE
    try:
        rate = int(v)
    except (TypeError, ValueError):
        raise InvoiceError(422, "vat_rate: целое число 0..100") from None
    if not 0 <= rate <= 100:
        raise InvoiceError(422, "vat_rate: целое число 0..100")
    return rate


def split_amount(total: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """Разбивка суммы акта по договорам пропорционально расходу строк; остаток
    округления — на последний договор, чтобы Σ частей = total копейка в копейку."""
    keys = list(weights)
    if not keys:
        return {}
    wsum = sum(weights.values(), Decimal(0))
    out: dict[str, Decimal] = {}
    acc = Decimal("0.00")
    for k in keys[:-1]:
        part = _q2(total * weights[k] / wsum) if wsum else _q2(total / len(keys))
        out[k] = part
        acc += part
    out[keys[-1]] = _q2(total - acc)
    return out


def amount_diff_pct(invoice_amount: Any, fact_amount: Any) -> float | None:
    fact, inv = Decimal(str(fact_amount or 0)), Decimal(str(invoice_amount or 0))
    if fact == 0:
        return None if inv == 0 else 100.0
    return float(abs(inv - fact) / fact * 100)


def parse_date(v: Any, field: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        raise InvoiceError(422, f"{field}: ожидается дата YYYY-MM-DD") from None


async def _owner_multiplier(pool, owner_id: str) -> float:
    """Цена клиента = net × множитель владельца договора (rbac.spend_multiplier —
    единственная точка «какую цену видит пользователь», B14b)."""
    import rbac

    role = await pool.fetchval("SELECT role FROM users WHERE id = $1", owner_id)
    return await rbac.spend_multiplier(str(owner_id), role or "")


async def roles_for_contract(pool, contract: dict) -> tuple[str, str]:
    """Роли сторон акта по counterparties.role заказчика/исполнителя договора."""
    row = await pool.fetchrow(
        """SELECT cl.role AS client_role, ct.role AS contractor_role
             FROM ord_contracts c
             JOIN counterparties cl ON cl.id = c.client_id
             JOIN counterparties ct ON ct.id = c.contractor_id
            WHERE c.id = $1""",
        contract["id"],
    )
    if not row:
        return "advertiser", "ors"
    return (row["client_role"] if row["client_role"] in ROLES else "advertiser",
            row["contractor_role"] if row["contractor_role"] in ROLES else "ors")


async def contract_is_executive(pool, contract: dict) -> bool:
    """Исполнительский договор ОРС-модели: исполнитель — наша организация
    (is_self) либо контрагент с ролью ОРС."""
    return bool(await pool.fetchval(
        "SELECT is_self OR role = 'ors' FROM counterparties WHERE id = $1", contract["contractor_id"]
    ))


# ------------------------------------------------------------------ preview --
# Версия erid на конец часа — тот же LATERAL, что в /cron/ord-export: показы
# делятся между версиями по часу действия. Строка принадлежит акту, если договор
# акта — исполнительский договор кампании (ОРС-модель) либо договор, на который
# зарегистрирована версия (режим self и акты до B13d). Договор строки
# (contract_id) — изначальный договор кампании, иначе договор версии.
_FACT_SQL = """
    SELECT s.creative_id, oc.version, oc.erid,
           COALESCE(cm.initial_contract_id, oc.contract_id) AS contract_id,
           to_char(s.ts_hour, 'YYYY-MM') AS period,
           min(li.bid_type) AS payment_type,
           sum(s.impressions) AS impressions,
           sum(s.clicks)      AS clicks,
           sum(s.spend)       AS spend
      FROM stats_hourly s
      JOIN LATERAL (
          SELECT version, erid, contract_id FROM ord_creatives o
          WHERE o.creative_id = s.creative_id
            AND o.erid IS NOT NULL AND o.erid <> ''
            AND o.status IN ('registered', 'erir_error', 'superseded')
            AND COALESCE(o.registered_at, o.created_at) < s.ts_hour + interval '1 hour'
          ORDER BY COALESCE(o.registered_at, o.created_at) DESC LIMIT 1
      ) oc ON true
      LEFT JOIN campaigns cm ON cm.id = s.campaign_id
      LEFT JOIN line_items li ON li.id = s.line_item_id
     WHERE (cm.executive_contract_id = $1 OR oc.contract_id = $1)
       AND NOT """ + foreign_campaign_sql("s.campaign_id") + """
       AND s.ts_hour >= $2::date AND s.ts_hour < ($3::date + interval '1 day')
       AND (cardinality($4::uuid[]) = 0 OR s.campaign_id = ANY($4::uuid[]))
     GROUP BY s.creative_id, oc.version, oc.erid, COALESCE(cm.initial_contract_id, oc.contract_id),
              to_char(s.ts_hour, 'YYYY-MM')
    HAVING sum(s.impressions) > 0 OR sum(s.clicks) > 0
     ORDER BY s.creative_id, oc.version, period
"""


async def contract_views(pool, contract_ids: list[str]) -> dict[str, dict]:
    """Краткие карточки договоров для разбивки акта (номер, стороны, регистрация в ОРД)."""
    if not contract_ids:
        return {}
    rows = await pool.fetch(
        """SELECT c.id, c.number, c.ord_external_id, c.ord_status, c.is_vat,
                  cl.name AS client_name, cl.inn AS client_inn,
                  ct.name AS contractor_name, ct.inn AS contractor_inn
             FROM ord_contracts c
             JOIN counterparties cl ON cl.id = c.client_id
             JOIN counterparties ct ON ct.id = c.contractor_id
            WHERE c.id = ANY($1::uuid[])""",
        contract_ids,
    )
    return {
        str(r["id"]): {
            "id": str(r["id"]), "number": r["number"], "ord_status": r["ord_status"],
            "ord_external_id": r["ord_external_id"],
            "client": {"name": r["client_name"], "inn": r["client_inn"]},
            "contractor": {"name": r["contractor_name"], "inn": r["contractor_inn"]},
        }
        for r in rows
    }


def group_by_contract(items: list[dict], spend_key: str = "spend") -> dict[str, Decimal]:
    """Σ расхода строк по изначальному договору (порядок — по первому появлению)."""
    out: dict[str, Decimal] = {}
    for it in items:
        cid = str(it["contract_id"])
        out[cid] = out.get(cid, Decimal("0.00")) + Decimal(str(it[spend_key] or 0))
    return out


async def fact_rows(pool, contract_id: str, start: date, end: date, campaign_ids: list[str]) -> list[dict]:
    rows = await pool.fetch(_FACT_SQL, contract_id, start, end, campaign_ids or [])
    return [dict(r) for r in rows]


async def preview(pool, contract: dict, start: date, end: date, campaign_ids: list[str] | None,
                  *, exclude_invoice_id: str | None = None) -> dict:
    """Строки акта по факту + предупреждения. Ничего не пишет."""
    slices = month_slices(start, end)
    if (end - start).days >= MAX_PERIOD_DAYS:
        raise InvoiceError(422, f"Период акта не может превышать {MAX_PERIOD_DAYS} дней")
    bounds = {p: (s, e) for p, s, e in slices}
    mult = Decimal(str(await _owner_multiplier(pool, str(contract["user_id"]))))
    camp_ids = list(campaign_ids or [])
    facts = await fact_rows(pool, str(contract["id"]), start, end, camp_ids)

    names = {}
    if facts:
        cr_rows = await pool.fetch(
            "SELECT id, name, campaign_id FROM creatives WHERE id = ANY($1::uuid[])",
            list({r["creative_id"] for r in facts}),
        )
        names = {str(r["id"]): (r["name"], str(r["campaign_id"]) if r["campaign_id"] else None) for r in cr_rows}

    items: list[dict] = []
    for r in facts:
        cid, ver, period = str(r["creative_id"]), int(r["version"]), r["period"]
        imps, clicks = int(r["impressions"] or 0), int(r["clicks"] or 0)
        spend = _q2(Decimal(str(r["spend"] or 0)) * mult)
        ptype = r["payment_type"] if r["payment_type"] in PAYMENT_TYPES else "cpm"
        name, camp = names.get(cid, (None, None))
        items.append({
            "creative_id": cid, "creative_name": name, "campaign_id": camp,
            "contract_id": str(r["contract_id"]) if r["contract_id"] else str(contract["id"]),
            "version": ver, "erid": r["erid"], "period": period,
            "period_start": bounds[period][0].isoformat(), "period_end": bounds[period][1].isoformat(),
            "payment_type": ptype,
            "fact_impressions": imps, "fact_clicks": clicks, "fact_spend": str(spend),
            "impressions": imps, "clicks": clicks, "spend": str(spend),
            "unit_cost": str(unit_cost(ptype, imps, clicks, spend)),
            "external_statistic_id": external_statistic_id(cid, ver, period),
        })

    # (креатив, версия, месяц) уже в другом акте — в этот его включить нельзя
    taken = await pool.fetch(
        """SELECT it.external_statistic_id, it.invoice_id, i.number, i.status
             FROM ord_invoice_items it JOIN ord_invoices i ON i.id = it.invoice_id
            WHERE it.external_statistic_id = ANY($1::text[]) AND ($2::uuid IS NULL OR it.invoice_id <> $2::uuid)""",
        [it["external_statistic_id"] for it in items], exclude_invoice_id,
    )
    taken_map = {r["external_statistic_id"]: r for r in taken}
    already: list[dict] = []
    for it in items:
        t = taken_map.get(it["external_statistic_id"])
        it["in_invoice"] = None
        if t:
            it["in_invoice"] = {"id": str(t["invoice_id"]), "number": t["number"], "status": t["status"]}
            already.append({"external_statistic_id": it["external_statistic_id"], **it["in_invoice"]})

    # Показы были у креативов контрагента договора, а действующего erid нет —
    # в акт такое не попадёт, но молчать нельзя (немаркированная реклама).
    without = await pool.fetch(
        """SELECT s.creative_id, c.name, sum(s.impressions) AS impressions
             FROM stats_hourly s JOIN creatives c ON c.id = s.creative_id
             LEFT JOIN campaigns cm ON cm.id = c.campaign_id
            WHERE c.user_id = $1
              AND (cm.executive_contract_id = $2 OR cm.initial_contract_id = $2 OR c.contract_id = $2)
              AND NOT """ + foreign_campaign_sql("s.campaign_id") + """
              AND s.ts_hour >= $3::date AND s.ts_hour < ($4::date + interval '1 day')
              AND (cardinality($5::uuid[]) = 0 OR s.campaign_id = ANY($5::uuid[]))
              AND NOT EXISTS (
                  SELECT 1 FROM ord_creatives o
                   WHERE o.creative_id = s.creative_id AND o.erid IS NOT NULL AND o.erid <> ''
                     AND o.status IN ('registered', 'erir_error', 'superseded')
                     AND COALESCE(o.registered_at, o.created_at) < s.ts_hour + interval '1 hour')
            GROUP BY s.creative_id, c.name HAVING sum(s.impressions) > 0""",
        contract["user_id"], contract["id"], start, end, camp_ids,
    )
    client_role, contractor_role = await roles_for_contract(pool, contract)
    is_vat = bool(contract.get("is_vat", True))
    vat_rate = DEFAULT_VAT_RATE if is_vat else 0
    includable = [it for it in items if not it["in_invoice"]]
    amount_gross = sum((Decimal(it["spend"]) for it in includable), Decimal("0.00"))
    stats_gross = sum((Decimal(it["fact_spend"]) for it in items), Decimal("0.00"))
    by_contract = group_by_contract(includable)
    views = await contract_views(pool, list(by_contract))
    contracts = [{
        **views.get(cid, {"id": cid, "number": None, "ord_status": None, "ord_external_id": None}),
        "amount_gross": str(_q2(total)), "amount_net": str(net_of(total, is_vat, vat_rate)), "vat_rate": vat_rate,
        "items_count": sum(1 for it in includable if it["contract_id"] == cid),
    } for cid, total in by_contract.items()]
    warnings: dict[str, Any] = {
        "already_invoiced": already,
        "without_erid": [
            {"creative_id": str(r["creative_id"]), "name": r["name"], "impressions": int(r["impressions"])}
            for r in without
        ],
        "contracts_unregistered": [c["id"] for c in contracts if c["ord_status"] != "registered"],
    }
    if not await contract_is_executive(pool, contract):
        warnings["contract_not_executive"] = True
    return {
        "contract_id": str(contract["id"]),
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "months": [p for p, _, _ in slices],
        "client_role": client_role, "contractor_role": contractor_role,
        "is_vat": is_vat, "vat_rate": vat_rate,
        "spend_multiplier": float(mult),
        "items": items,
        "contracts": contracts,
        "amount_gross": str(_q2(amount_gross)),
        "amount_net": str(net_of(amount_gross, is_vat, vat_rate)),
        "statistics_amount_gross": str(_q2(stats_gross)),
        "statistics_amount_net": str(net_of(stats_gross, is_vat, vat_rate)),
        "warnings": warnings,
    }


# --------------------------------------------------------------------- save --
def _int_field(it: dict, key: str, default: int = 0) -> int:
    v = it.get(key)
    if v is None or v == "":
        v = default
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise InvoiceError(422, f"items.{key}: целое число") from None
    if v < 0:
        raise InvoiceError(422, f"items.{key}: не может быть отрицательным")
    return v


async def save(pool, *, invoice_id: str | None, contract: dict, data: dict, items: list[dict],
               actor_id: str | None) -> dict:
    """Создать/обновить акт с построчной детализацией (черновик).

    Валидация: период, роли, уникальность номера в договоре, каждая строка —
    версия erid этого договора, месяц строки внутри периода акта, строка не
    входит в другой акт (409). Факт (fact_*) пересчитывается при каждом
    сохранении — акт хранит и то, что было в системе, и то, что подписали.
    """
    start = parse_date(data.get("period_start"), "period_start")
    end = parse_date(data.get("period_end"), "period_end")
    inv_date = parse_date(data.get("invoice_date"), "invoice_date")
    slices = month_slices(start, end)
    if (end - start).days >= MAX_PERIOD_DAYS:
        raise InvoiceError(422, f"Период акта не может превышать {MAX_PERIOD_DAYS} дней")
    bounds = {p: (s, e) for p, s, e in slices}
    number = (data.get("number") or "").strip()
    if not number:
        raise InvoiceError(422, "number: номер акта обязателен")
    default_roles = await roles_for_contract(pool, contract)
    client_role = data.get("client_role") or default_roles[0]
    contractor_role = data.get("contractor_role") or default_roles[1]
    if client_role not in ROLES or contractor_role not in ROLES:
        raise InvoiceError(422, "client_role/contractor_role: advertiser | agency | publisher | ors")
    is_vat = bool(contract.get("is_vat", True) if data.get("is_vat") is None else data.get("is_vat"))
    vat_rate = parse_vat_rate(data.get("vat_rate"), is_vat)
    camp_ids = [str(c) for c in (data.get("campaign_ids") or [])]
    if not items:
        raise InvoiceError(422, "items: акт без строк сохранить нельзя")

    owner = str(contract["user_id"])
    mult = Decimal(str(await _owner_multiplier(pool, owner)))
    facts = {
        (str(r["creative_id"]), int(r["version"]), r["period"]): r
        for r in await fact_rows(pool, str(contract["id"]), start, end, camp_ids)
    }

    prepared: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        cid, period = str(raw.get("creative_id") or ""), str(raw.get("period") or "")
        ver = _int_field(raw, "version", 0)
        if period not in bounds:
            raise InvoiceError(422, f"items.period {period!r} вне периода акта")
        ver_row = await pool.fetchrow(
            """SELECT oc.erid, oc.contract_id, c.user_id, cm.executive_contract_id, cm.initial_contract_id
                 FROM ord_creatives oc JOIN creatives c ON c.id = oc.creative_id
                 LEFT JOIN campaigns cm ON cm.id = c.campaign_id
                WHERE oc.creative_id = $1 AND oc.version = $2 AND oc.erid IS NOT NULL AND oc.erid <> ''""",
            cid, ver,
        )
        if not ver_row or str(ver_row["user_id"]) != owner:
            raise InvoiceError(404, f"Креатив {cid} v{ver}: версия с erid не найдена")
        inv_ct = str(contract["id"])
        if inv_ct not in (str(ver_row["contract_id"]), str(ver_row["executive_contract_id"] or "")):
            raise InvoiceError(409, f"Креатив {cid} v{ver}: договор акта не является ни исполнительским договором "
                                    "кампании, ни договором регистрации версии")
        row_contract = str(ver_row["initial_contract_id"] or ver_row["contract_id"] or inv_ct)
        sid = external_statistic_id(cid, ver, period)
        if sid in seen:
            raise InvoiceError(422, f"Строка {sid} указана дважды")
        seen.add(sid)
        fact = facts.get((cid, ver, period))
        f_imps = int(fact["impressions"] or 0) if fact else 0
        f_clicks = int(fact["clicks"] or 0) if fact else 0
        f_spend = _q2(Decimal(str(fact["spend"] or 0)) * mult) if fact else Decimal("0.00")
        ptype = raw.get("payment_type") or (fact["payment_type"] if fact else None) or "cpm"
        if ptype not in PAYMENT_TYPES:
            raise InvoiceError(422, "items.payment_type: cpm | cpc")
        imps = _int_field(raw, "impressions", f_imps)
        clicks = _int_field(raw, "clicks", f_clicks)
        spend = _q2(raw["spend"]) if raw.get("spend") not in (None, "") else f_spend
        if spend < 0:
            raise InvoiceError(422, "items.spend: не может быть отрицательным")
        uc = _q4(raw["unit_cost"]) if raw.get("unit_cost") not in (None, "") else unit_cost(ptype, imps, clicks, spend)
        prepared.append({
            "creative_id": cid, "version": ver, "erid": ver_row["erid"], "period": period,
            "contract_id": row_contract,
            "period_start": bounds[period][0], "period_end": bounds[period][1], "payment_type": ptype,
            "fact_impressions": f_imps, "fact_clicks": f_clicks, "fact_spend": f_spend,
            "impressions": imps, "clicks": clicks, "spend": spend, "unit_cost": uc,
            "external_statistic_id": sid,
        })

    taken = await pool.fetch(
        """SELECT it.external_statistic_id, i.number FROM ord_invoice_items it
             JOIN ord_invoices i ON i.id = it.invoice_id
            WHERE it.external_statistic_id = ANY($1::text[]) AND ($2::uuid IS NULL OR it.invoice_id <> $2::uuid)""",
        [p["external_statistic_id"] for p in prepared], invoice_id,
    )
    if taken:
        t = taken[0]
        raise InvoiceError(409, f"Строка {t['external_statistic_id']} уже входит в акт № {t['number']}")

    raw_amount = data.get("amount_gross")
    if raw_amount in (None, ""):
        raw_amount = data.get("amount")  # старое имя поля (B13b)
    amount = _q2(raw_amount) if raw_amount not in (None, "") else sum((p["spend"] for p in prepared), Decimal("0.00"))
    if amount < 0:
        raise InvoiceError(422, "amount_gross: не может быть отрицательной")
    amount_net = net_of(amount, is_vat, vat_rate)

    # Разбивка по изначальным договорам: явная (contracts[]) — должна покрывать
    # ровно договоры строк и сходиться с суммой акта; иначе пропорционально расходу.
    by_contract = group_by_contract(prepared)
    explicit = data.get("contracts") or []
    if explicit:
        given: dict[str, Decimal] = {}
        for c in explicit:
            cid_ = str(c.get("initial_contract_id") or "")
            if cid_ not in by_contract:
                raise InvoiceError(422, f"contracts: договор {cid_} не относится к строкам акта")
            given[cid_] = _q2(c.get("amount_gross"))
        if set(given) != set(by_contract):
            raise InvoiceError(422, "contracts: укажите сумму для каждого договора строк акта")
        if sum(given.values(), Decimal("0.00")) != amount:
            raise InvoiceError(422, "contracts: сумма по договорам должна совпадать с суммой акта")
        split = {cid_: given[cid_] for cid_ in by_contract}
    else:
        split = split_amount(amount, by_contract)

    async with pool.acquire() as conn, conn.transaction():
        dup = await conn.fetchval(
            "SELECT id FROM ord_invoices WHERE contract_id = $1 AND number = $2 AND ($3::uuid IS NULL OR id <> $3::uuid)",
            contract["id"], number, invoice_id,
        )
        if dup:
            raise InvoiceError(409, f"Акт № {number} по этому договору уже существует")
        if invoice_id:
            row = await conn.fetchrow(
                """UPDATE ord_invoices
                      SET number=$2, invoice_date=$3, period_start=$4, period_end=$5,
                          amount_gross=$6, amount_net=$7, vat_rate=$8, is_vat=$9,
                          client_role=$10, contractor_role=$11, campaign_ids=$12::uuid[],
                          status='draft', error=NULL, updated_at=now()
                    WHERE id=$1 RETURNING *""",
                invoice_id, number, inv_date, start, end, amount, amount_net, vat_rate, is_vat,
                client_role, contractor_role, camp_ids,
            )
            await conn.execute("DELETE FROM ord_invoice_items WHERE invoice_id = $1", invoice_id)
            await conn.execute("DELETE FROM ord_invoice_contracts WHERE invoice_id = $1", invoice_id)
        else:
            row = await conn.fetchrow(
                """INSERT INTO ord_invoices (user_id, contract_id, number, invoice_date, period_start, period_end,
                                            amount_gross, amount_net, vat_rate, is_vat, client_role, contractor_role,
                                            campaign_ids, created_by)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::uuid[],$14) RETURNING *""",
                owner, contract["id"], number, inv_date, start, end, amount, amount_net, vat_rate, is_vat,
                client_role, contractor_role, camp_ids, actor_id,
            )
        await conn.executemany(
            """INSERT INTO ord_invoice_items
                 (invoice_id, creative_id, version, erid, period, period_start, period_end, payment_type,
                  fact_impressions, fact_clicks, fact_spend, impressions, clicks, spend, unit_cost,
                  external_statistic_id, contract_id)
               VALUES ($1,$2::uuid,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::uuid)""",
            [(row["id"], p["creative_id"], p["version"], p["erid"], p["period"], p["period_start"],
              p["period_end"], p["payment_type"], p["fact_impressions"], p["fact_clicks"], p["fact_spend"],
              p["impressions"], p["clicks"], p["spend"], p["unit_cost"], p["external_statistic_id"],
              p["contract_id"])
             for p in prepared],
        )
        await conn.executemany(
            """INSERT INTO ord_invoice_contracts (invoice_id, initial_contract_id, amount_gross, amount_net, vat_rate)
               VALUES ($1,$2::uuid,$3,$4,$5)""",
            [(row["id"], cid_, gross, net_of(gross, is_vat, vat_rate), vat_rate) for cid_, gross in split.items()],
        )
    return await load(pool, str(row["id"]))


async def load(pool, invoice_id: str) -> dict | None:
    inv = await pool.fetchrow(
        """SELECT i.*, c.number AS contract_number, c.ord_external_id AS contract_external_id,
                  c.ord_status AS contract_ord_status
             FROM ord_invoices i JOIN ord_contracts c ON c.id = i.contract_id
            WHERE i.id = $1""",
        invoice_id,
    )
    if not inv:
        return None
    items = await pool.fetch(
        """SELECT it.*, c.name AS creative_name, c.campaign_id
             FROM ord_invoice_items it LEFT JOIN creatives c ON c.id = it.creative_id
            WHERE it.invoice_id = $1 ORDER BY it.creative_id, it.version, it.period""",
        invoice_id,
    )
    out = dict(inv)
    out["items"] = [dict(r) for r in items]
    splits = await pool.fetch(
        "SELECT * FROM ord_invoice_contracts WHERE invoice_id = $1 ORDER BY initial_contract_id", invoice_id
    )
    views = await contract_views(pool, [str(r["initial_contract_id"]) for r in splits])
    out["contracts"] = [{
        **views.get(str(r["initial_contract_id"]), {"id": str(r["initial_contract_id"])}),
        "initial_contract_id": str(r["initial_contract_id"]),
        "amount_gross": r["amount_gross"], "amount_net": r["amount_net"], "vat_rate": r["vat_rate"],
        "items_count": sum(1 for it in out["items"] if str(it["contract_id"]) == str(r["initial_contract_id"])),
    } for r in splits]
    stats_gross = sum((Decimal(str(it["fact_spend"] or 0)) for it in out["items"]), Decimal("0.00"))
    out["statistics_amount_gross"] = _q2(stats_gross)
    out["statistics_amount_net"] = net_of(stats_gross, bool(out["is_vat"]), int(out["vat_rate"]))
    pct = amount_diff_pct(out["amount_gross"], stats_gross)
    out["amount_diff_pct"] = None if pct is None else round(pct, 2)
    out["warnings"] = {}
    if pct is not None and pct > AMOUNT_DIFF_WARN_PCT:
        out["warnings"]["amount_diff"] = {
            "invoice": str(_q2(out["amount_gross"])), "statistics": str(_q2(stats_gross)), "pct": round(pct, 2),
        }
    return out


# ------------------------------------------------------------------- submit --
async def submit(pool, invoice: dict) -> dict:
    """Отправка акта: invoice (upsert) → link creatives (пересборка) → statistic
    построчно по месяцам. Статус акта ok | partial | failed; строки accepted |
    rejected с причиной. Повтор после редактирования — та же цепочка.
    """
    inv_id = str(invoice["id"])
    full = await load(pool, inv_id)
    if not full:
        raise InvoiceError(404, "Акт не найден")
    if not full["items"]:
        raise InvoiceError(409, "Акт без строк отправить нельзя")
    if full["contract_ord_status"] != "registered" or not full["contract_external_id"]:
        raise InvoiceError(409, "Сначала зарегистрируйте договор в ОРД")
    if full["status"] == "submitting":
        raise InvoiceError(409, "Акт уже отправляется")
    for c in full["contracts"]:
        if c.get("ord_status") != "registered" or not c.get("ord_external_id"):
            raise InvoiceError(409, f"Договор № {c.get('number') or c['initial_contract_id']} не зарегистрирован в ОРД")
    ext_by_contract = {c["initial_contract_id"]: c["ord_external_id"] for c in full["contracts"]}

    await pool.execute(
        "UPDATE ord_invoices SET status='submitting', error=NULL, updated_at=now() WHERE id=$1", inv_id
    )

    async def _fail(err: str) -> dict:
        await pool.execute(
            """UPDATE ord_invoices SET status='failed', error=$2, submitted_at=now(), updated_at=now()
                WHERE id=$1""",
            inv_id, err,
        )
        await pool.execute(
            "UPDATE ord_invoice_items SET status='rejected', error=$2 WHERE invoice_id=$1", inv_id, err
        )
        return await load(pool, inv_id)

    contract_ext = full["contract_external_id"]
    try:
        adapter = await get_ord_adapter()
        await ensure_platform(pool, adapter)
        await adapter.register_invoice({
            "external_id": inv_id, "contract_external_id": contract_ext,
            "number": full["number"], "invoice_date": full["invoice_date"],
            "period_start": full["period_start"], "period_end": full["period_end"],
            "amount": full["amount_gross"], "amount_gross": full["amount_gross"], "amount_net": full["amount_net"],
            "vat_rate": full["vat_rate"], "is_vat": full["is_vat"],
            "client_role": full["client_role"], "contractor_role": full["contractor_role"],
            "contracts": [{
                "contract_external_id": c["ord_external_id"],
                "amount_gross": c["amount_gross"], "amount_net": c["amount_net"],
                "vat_rate": c["vat_rate"], "is_vat": full["is_vat"],
            } for c in full["contracts"]],
        })
        groups: dict[str, set[str]] = {}
        for it in full["items"]:
            ext = ext_by_contract.get(str(it["contract_id"]), contract_ext)
            groups.setdefault(ext, set()).add(external_creative_id(str(it["creative_id"]), int(it["version"])))
        await adapter.link_invoice_creatives({
            "external_invoice_id": inv_id, "contract_external_id": contract_ext,
            "external_creative_ids": sorted(set().union(*groups.values())) if groups else [],
            "groups": [{"contract_external_id": ext, "external_creative_ids": sorted(ids)}
                       for ext, ids in sorted(groups.items())],
        })
    except OrdError as e:
        return await _fail(str(e))

    by_period: dict[str, list[dict]] = {}
    for it in full["items"]:
        by_period.setdefault(it["period"], []).append({
            "external_statistic_id": it["external_statistic_id"],
            "external_creative_id": external_creative_id(str(it["creative_id"]), int(it["version"])),
            "date_start": it["period_start"].isoformat(),
            "date_end": it["period_end"].isoformat(),
            "impressions": int(it["fact_impressions"]),
            "clicks": int(it["fact_clicks"]),
            "invoice_impressions": int(it["impressions"]),
            "invoice_spend": str(it["spend"]),
            "unit_cost": str(it["unit_cost"]),
            "payment_type": it["payment_type"],
            "with_vat": bool(full["is_vat"]),
            "vat_rate": full["vat_rate"],
        })
    accepted: set[str] = set()
    rejected: dict[str, str] = {}
    responses: dict[str, Any] = {}
    for period, rows in sorted(by_period.items()):
        try:
            out = await adapter.submit_stats(period, rows)
        except OrdError as e:
            for r in rows:
                rejected[r["external_statistic_id"]] = str(e)
            responses[period] = {"error": str(e)}
            continue
        responses[period] = out
        accepted.update(out.get("accepted") or [])
        for r in out.get("rejected") or []:
            rejected[r["id"]] = r.get("error") or "rejected"
        for r in rows:
            sid = r["external_statistic_id"]
            if sid not in accepted and sid not in rejected:
                rejected[sid] = "no acknowledgement from provider"

    async with pool.acquire() as conn, conn.transaction():
        if accepted:
            await conn.execute(
                """UPDATE ord_invoice_items SET status='accepted', error=NULL
                    WHERE invoice_id=$1 AND external_statistic_id = ANY($2::text[])""",
                inv_id, list(accepted),
            )
        for sid, err in rejected.items():
            await conn.execute(
                "UPDATE ord_invoice_items SET status='rejected', error=$3 WHERE invoice_id=$1 AND external_statistic_id=$2",
                inv_id, sid, err,
            )
        status = "ok" if not rejected else ("failed" if not accepted else "partial")
        err = None if status == "ok" else f"{len(rejected)} строк отклонено ОРД"
        await conn.execute(
            """UPDATE ord_invoices
                  SET status=$2, error=$3, ord_provider=$4, ord_external_id=$5,
                      submitted_at=now(), updated_at=now()
                WHERE id=$1""",
            inv_id, status, err, adapter.provider, inv_id,
        )
        if status == "ok":
            # B13g: акт принят ОРД повторно — открытые ошибки ЕРИР по нему закрыты
            from .notifications import resolve_for

            await resolve_for(conn, "invoice", str(inv_id))
    result = await load(pool, inv_id)
    result["ord_response"] = json.loads(json.dumps(responses, ensure_ascii=False, default=str))
    return result
