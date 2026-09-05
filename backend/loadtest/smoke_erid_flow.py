"""Сквозной smoke живого backend: модерация → ОРД → erid → фид биддера.

Две роли, как в проде: manager владеет кампанией/контрагентами/договором/
креативами, admin только модерирует. Отдельный блок — admin ведёт кампанию сам.

Запуск на dev-стенде (REGISTRATION_MODE=open, mock-ОРД):

    BASE_URL=http://127.0.0.1:8000 RTB_API_TOKEN=... \
    PSQL_CMD="psql -h ... -d dsp -qc" python backend/loadtest/smoke_erid_flow.py

PSQL_CMD нужен, чтобы поднять свежезарегистрированного пользователя до admin.
Либо задайте ADMIN_EMAIL/ADMIN_PASSWORD существующего админа.

Инварианты, которые нельзя нарушать в проде:
  1. владелец не может сам поставить approved;
  2. approve без договора → awaiting_contract, креатив НЕ в фиде;
  3. ручная регистрация с автоподбором договора → erid → в фиде;
  4. approve при наличии договора → erid сразу; кампания проходит launch gate;
  5. смена содержимого одобренного (manager) → pending, erid снят, из фида ушёл;
  6. повторный approve → новый erid;
  7. admin создаёт креатив → approved + erid сразу (не минуя ОРД); один
     approved без erid блокирует запуск всей кампании (launch gate);
  8. admin меняет содержимое → статус approved, но erid перевыдан.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import httpx

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
FEED = {"Authorization": f"Bearer {os.environ.get('RTB_API_TOKEN', 'feed-token-dev')}"}
PSQL_CMD = os.environ.get("PSQL_CMD")
TAG = uuid.uuid4().hex[:6]
PW = "Str0ngPass!2026"


def step(name: str, r: httpx.Response, ok=(200, 201)):
    flag = "OK  " if r.status_code in ok else "FAIL"
    print(f"[{flag}] {name}: {r.status_code} {r.text[:200]}")
    if r.status_code not in ok:
        sys.exit(1)
    return r.json() if r.text else None


def feed_ids(c: httpx.Client) -> set[str]:
    data = step("feed", c.get("/rtb-feed/campaigns", headers=FEED))
    return {x["creative_id"] for x in data["candidates"]}


def session(email: str, password: str, *, register: bool, promote: str | None = None) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=20)
    if register:
        step(f"register {email}", c.post("/auth/register", json={"email": email, "password": password, "name": email.split("@")[0]}))
        if promote:
            if not PSQL_CMD:
                sys.exit("нужен PSQL_CMD (или ADMIN_EMAIL/ADMIN_PASSWORD), чтобы получить админа")
            subprocess.run([*PSQL_CMD.split(), f"UPDATE users SET role='{promote}' WHERE email='{email}'"], check=True)
    login = c.post("/auth/login", json={"email": email, "password": password})
    step(f"login {email}", login)
    # Cookie сессии ставится с Secure; на http-стенде httpx её отбросит —
    # переносим вручную из Set-Cookie (в проде за https не нужно).
    if BASE.startswith("http://"):
        name, _, rest = login.headers.get("set-cookie", "").partition("=")
        c.cookies.set(name.strip(), rest.split(";", 1)[0])
    return c


def setup_campaign(c: httpx.Client, label: str) -> dict:
    """Рекламодатель + кампания + активная RU-группа + зарегистрированные контрагенты."""
    adv = step("advertiser", c.post("/advertisers", json={"name": f"{label} Adv {TAG}", "balance": 10000, "currency": "RUB"}))
    camp = step("campaign", c.post("/campaigns", json={
        "advertiser_id": adv["id"], "name": f"{label} Camp {TAG}", "daily_budget": 1000, "total_budget": 10000,
        "start_date": "2026-01-01", "end_date": "2030-12-31"}))
    li = step("line item", c.post("/line-items", json={
        "campaign_id": camp["id"], "name": "LI", "bid_price": 50, "daily_budget": 500,
        "targeting": {"geo_countries": ["RU"]}}))
    step("li active", c.patch(f"/line-items/{li['id']}", json={"status": "active"}))
    cp_client = step("counterparty advertiser", c.post("/ord/counterparties", json={
        "name": "ООО Тест", "inn": "7707083893", "ogrn": "1027700132195", "role": "advertiser", "address": "Москва"}))
    cp_ors = step("counterparty ors", c.post("/ord/counterparties", json={"name": "Central DSP", "inn": "7701234567", "role": "ors"}))
    step("cp register advertiser", c.post(f"/ord/counterparties/{cp_client['id']}/register"))
    step("cp register ors", c.post(f"/ord/counterparties/{cp_ors['id']}/register"))
    banner = {
        "campaign_id": camp["id"], "line_item_id": li["id"], "type": "banner", "width": 300, "height": 250,
        "click_url": "https://example.com", "counterparty_id": cp_client["id"], "kktu_code": "1.1",
    }
    return {"camp": camp, "cp_client": cp_client, "cp_ors": cp_ors, "banner": banner}


def add_contract(c: httpx.Client, ctx: dict) -> None:
    ct = step("contract", c.post("/ord/contracts", json={
        "client_id": ctx["cp_client"]["id"], "contractor_id": ctx["cp_ors"]["id"],
        "number": f"D-{TAG}", "signed_at": "2026-08-01", "amount": 100000}))
    step("contract register", c.post(f"/ord/contracts/{ct['id']}/register"))


def manager_flow(mgr: httpx.Client, adm: httpx.Client) -> None:
    print("\n=== manager владеет, admin модерирует ===")
    ctx = setup_campaign(mgr, "Mgr")
    camp, banner = ctx["camp"], ctx["banner"]
    step("camp active without creative -> 409", mgr.patch(f"/campaigns/{camp['id']}", json={"status": "active"}), ok=(409,))

    cr1 = step("creative #1 (no contract yet)", mgr.post("/creatives", json={**banner, "name": "Banner 1", "asset_url": "https://example.com/b.png"}))
    assert cr1["status"] == "pending", cr1["status"]
    step("1. self-approve blocked", mgr.patch(f"/creatives/{cr1['id']}", json={"status": "approved"}), ok=(422,))

    ap1 = step("approve #1", adm.post(f"/moderation/{cr1['id']}/approve"))
    assert ap1["ord"]["status"] == "awaiting_contract", ap1["ord"]
    assert cr1["id"] not in feed_ids(mgr), "2. креатив без erid утёк в фид"
    print("    2. approve без договора → awaiting_contract, не в фиде")

    step("camp active with approved-but-unmarked creative -> 409", mgr.patch(f"/campaigns/{camp['id']}", json={"status": "active"}), ok=(409,))
    add_contract(mgr, ctx)
    reg = step("manual register (auto contract)", mgr.post(f"/ord/creatives/{cr1['id']}/register", json={}))
    assert reg["erid"]
    step("camp active (launch gate passes)", mgr.patch(f"/campaigns/{camp['id']}", json={"status": "active"}))
    assert cr1["id"] in feed_ids(mgr), "3. креатив с erid не попал в фид"
    print(f"    3. ручная регистрация → erid {reg['erid']} → launch gate пройден → в фиде")

    cr2 = step("creative #2", mgr.post("/creatives", json={**banner, "name": "Banner 2", "asset_url": "https://example.com/b2.png"}))
    ap2 = step("approve #2", adm.post(f"/moderation/{cr2['id']}/approve"))
    assert ap2["ord"]["status"] == "ok" and ap2["erid"], ap2["ord"]
    assert cr2["id"] in feed_ids(mgr)
    print(f"    4. approve с договором → erid {ap2['erid']} сразу, в фиде")

    ch = step("change asset of approved", mgr.patch(f"/creatives/{cr2['id']}", json={"asset_url": "https://example.com/other.png"}))
    assert ch["status"] == "pending" and not ch["erid"], (ch["status"], ch["erid"])
    assert cr2["id"] not in feed_ids(mgr)
    print("    5. смена содержимого → pending, erid снят, из фида убран")

    ap3 = step("re-approve #2", adm.post(f"/moderation/{cr2['id']}/approve"))
    assert ap3["ord"]["status"] == "ok" and ap3["erid"] and ap3["erid"] != ap2["erid"], (ap3["ord"], ap3.get("erid"), ap2.get("erid"))
    print(f"    6. повторный approve → новый erid {ap3['erid']}")


def admin_flow(adm: httpx.Client) -> None:
    print("\n=== admin ведёт кампанию сам ===")
    ctx = setup_campaign(adm, "Adm")
    banner = ctx["banner"]

    cr0 = step("admin creative without contract", adm.post("/creatives", json={**banner, "name": "Adm 0", "asset_url": "https://example.com/a0.png"}))
    assert cr0["status"] == "approved" and not cr0.get("erid") and cr0["ord"]["status"] == "awaiting_contract", cr0.get("ord")
    assert cr0["id"] not in feed_ids(adm), "approved без erid утёк в фид"
    print("    7a. без договора: approved, но awaiting_contract и не в фиде")

    add_contract(adm, ctx)
    cr = step("admin creative with contract", adm.post("/creatives", json={**banner, "name": "Adm 1", "asset_url": "https://example.com/a1.png"}))
    assert cr["status"] == "approved" and cr["erid"] and cr["ord"]["status"] == "ok", cr.get("ord")
    # Launch gate: один approved без erid блокирует всю кампанию (не только сам креатив).
    step("camp active blocked by unmarked Adm 0 -> 409", adm.patch(f"/campaigns/{ctx['camp']['id']}", json={"status": "active"}), ok=(409,))
    assert step("register Adm 0 manually", adm.post(f"/ord/creatives/{cr0['id']}/register", json={}))["erid"]
    step("camp active", adm.patch(f"/campaigns/{ctx['camp']['id']}", json={"status": "active"}))
    assert {cr["id"], cr0["id"]} <= feed_ids(adm)
    print(f"    7b. с договором: approved + erid {cr['erid']} сразу, в фиде")

    ch = step("admin changes asset", adm.patch(f"/creatives/{cr['id']}", json={"asset_url": "https://example.com/a2.png"}))
    assert ch["status"] == "approved" and ch["erid"] and ch["erid"] != cr["erid"], (ch["status"], ch.get("erid"))
    assert ch["ord"]["status"] == "ok"
    assert cr["id"] in feed_ids(adm)
    print(f"    8. смена содержимого админом → approved, erid перевыдан {ch['erid']}")


def main() -> None:
    if os.environ.get("ADMIN_EMAIL"):
        adm = session(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"], register=False)
    else:
        adm = session(f"admin-{TAG}@example.com", PW, register=True, promote="admin")
    me = step("me (admin)", adm.get("/auth/me"))
    assert me["role"] in ("admin", "superadmin"), me["role"]
    mgr = session(f"mgr-{TAG}@example.com", PW, register=True)
    assert step("me (manager)", mgr.get("/auth/me"))["role"] == "manager"

    manager_flow(mgr, adm)
    admin_flow(adm)
    print("\nSMOKE OK")


if __name__ == "__main__":
    main()
