"""Цепочка ОРД на кампании (ord_spec §7 D2, B13d).

Модель как в Avito: перед настройкой кампании пользователь отвечает «кого вы
рекламируете» (``ord_mode``), выбирает рекламодателя (``counterparty_id``) и
договоры, затем подтверждает цепочку (``ord_confirmed_at``). После подтверждения
поля заморожены (``409 ord_locked``): на них зарегистрированы erid.

Цепочка в терминах таблиц::

    counterparty(advertiser) --initial (client=advertiser, contractor=agency)-->
    counterparty(agency)     --executive (client=agency, contractor=self ОРС)-->
    counterparty(self, isOpc) --> сводная площадка Central DSP

* ``self``          — свои товары/услуги: договоров с посредниками нет, только
  исполнительский (client = рекламодатель, contractor = наша организация).
* ``own_client``    — свой клиент по прямому договору: изначальный
  (client = рекламодатель, contractor = агентство) + исполнительский
  (client = агентство, contractor = наша организация).
* ``partner_client``— клиент партнёра: как own_client, но между изначальным и
  исполнительским может стоять посреднический договор
  (``ord_contracts.parent_contract_id`` у исполнительского).

Креатив регистрируется на **изначальный** договор (в ``self`` — на
исполнительский): это договор с конечным рекламодателем, по нему ОРД строит
цепочку до площадки.

Валидация чистая (``validate_chain``) — покрыта unit-тестами без БД; загрузка
объектов и подтверждение — ``load_chain``/``confirm_chain``.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

MODES = ("self", "own_client", "partner_client")
CHAIN_FIELDS = ("ord_mode", "counterparty_id", "initial_contract_id", "executive_contract_id")

MODE_LABELS = {
    "self": "Свои товары и услуги",
    "own_client": "Своего клиента (прямой договор)",
    "partner_client": "Клиента партнёра (через посредника)",
}

# Коды проблем — стабильные, UI показывает подписи из PROBLEM_LABELS.
PROBLEM_LABELS = {
    "mode_missing": "Не выбрано, кого вы рекламируете",
    "mode_invalid": "Неизвестный режим цепочки",
    "counterparty_missing": "Не выбран рекламодатель",
    "counterparty_unregistered": "Рекламодатель не зарегистрирован в ОРД",
    "self_promotion_not_supported": "Реклама самой рекламной системы (самореклама) пока не поддерживается",
    "self_org_missing": "Не задана наша организация (ОРС) — отметьте её в разделе «Маркировка (ОРД)»",
    "initial_missing": "Не выбран изначальный договор с рекламодателем",
    "initial_not_allowed": "В режиме «свои товары» изначальный договор не нужен",
    "initial_client_mismatch": "Заказчик изначального договора должен быть рекламодателем",
    "initial_unregistered": "Изначальный договор не зарегистрирован в ОРД",
    "executive_missing": "Не выбран исполнительский договор с рекламной системой",
    "executive_contractor_mismatch": "Исполнителем по исполнительскому договору должна быть наша организация (ОРС)",
    "executive_client_mismatch": "Заказчик исполнительского договора должен быть исполнителем по изначальному",
    "executive_client_not_advertiser": "Заказчиком исполнительского договора должен быть рекламодатель",
    "executive_unregistered": "Исполнительский договор не зарегистрирован в ОРД",
}


def _id(row: dict[str, Any] | None, key: str = "id") -> str | None:
    if not row or row.get(key) is None:
        return None
    return str(row[key])


def validate_chain(mode: str | None, counterparty: dict | None, initial: dict | None,
                   executive: dict | None, self_org: dict | None) -> list[str]:
    """Список кодов проблем; пустой список = цепочку можно подтвердить."""
    problems: list[str] = []
    if not mode:
        return ["mode_missing"]
    if mode not in MODES:
        return ["mode_invalid"]
    if not counterparty:
        problems.append("counterparty_missing")
    elif counterparty.get("is_self"):
        # isSelfPromotion в ОРД — отдельный payload без договоров; см. ord_spec §6 [V].
        return ["self_promotion_not_supported"]
    elif counterparty.get("ord_status") != "registered":
        problems.append("counterparty_unregistered")
    if not self_org:
        problems.append("self_org_missing")

    if mode == "self":
        if initial:
            problems.append("initial_not_allowed")
        if not executive:
            problems.append("executive_missing")
        else:
            if counterparty and _id(executive, "client_id") != _id(counterparty):
                problems.append("executive_client_not_advertiser")
            if self_org and _id(executive, "contractor_id") != _id(self_org):
                problems.append("executive_contractor_mismatch")
            if executive.get("ord_status") != "registered":
                problems.append("executive_unregistered")
        return problems

    if not initial:
        problems.append("initial_missing")
    else:
        if counterparty and _id(initial, "client_id") != _id(counterparty):
            problems.append("initial_client_mismatch")
        if initial.get("ord_status") != "registered":
            problems.append("initial_unregistered")
    if not executive:
        problems.append("executive_missing")
    else:
        if self_org and _id(executive, "contractor_id") != _id(self_org):
            problems.append("executive_contractor_mismatch")
        # partner_client: между договорами может стоять посреднический,
        # тогда исполнительский ссылается на него через parent_contract_id.
        via_intermediary = mode == "partner_client" and executive.get("parent_contract_id")
        if initial and not via_intermediary and _id(executive, "client_id") != _id(initial, "contractor_id"):
            problems.append("executive_client_mismatch")
        if executive.get("ord_status") != "registered":
            problems.append("executive_unregistered")
    return problems


async def assert_counterparty_ref(conn, cp_id, owner_id, not_found: str = "Контрагент не найден") -> dict:
    """Контрагент в ссылочном поле: свой (владельца объекта) либо наша
    организация (ОРС) — она общая для всех, на неё заключается исполнительский
    договор любого клиента."""
    row = await conn.fetchrow(
        "SELECT * FROM counterparties WHERE id = $1 AND (user_id = $2 OR is_self)", cp_id, str(owner_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail=not_found)
    return dict(row)


def registration_contract(campaign: dict, initial: dict | None, executive: dict | None) -> dict | None:
    """Договор, на который регистрируется креатив кампании."""
    if campaign.get("ord_mode") == "self":
        return executive
    return initial


async def _fetch_contract(conn, contract_id) -> dict | None:
    if not contract_id:
        return None
    row = await conn.fetchrow(
        """SELECT c.*, cl.name AS client_name, cl.inn AS client_inn,
                  ct.name AS contractor_name, ct.inn AS contractor_inn
             FROM ord_contracts c
             JOIN counterparties cl ON cl.id = c.client_id
             JOIN counterparties ct ON ct.id = c.contractor_id
            WHERE c.id = $1""",
        contract_id,
    )
    return dict(row) if row else None


async def _fetch_counterparty(conn, cp_id) -> dict | None:
    if not cp_id:
        return None
    row = await conn.fetchrow("SELECT * FROM counterparties WHERE id = $1", cp_id)
    return dict(row) if row else None


def _cp_view(cp: dict | None) -> dict | None:
    if not cp:
        return None
    return {"id": str(cp["id"]), "name": cp["name"], "inn": cp.get("inn"), "legal_type": cp.get("legal_type"),
            "ord_status": cp.get("ord_status"), "is_self": bool(cp.get("is_self"))}


def _contract_view(c: dict | None) -> dict | None:
    if not c:
        return None
    return {
        "id": str(c["id"]), "number": c["number"],
        "signed_at": c["signed_at"].isoformat() if c.get("signed_at") else None,
        "contract_type": c.get("contract_type"), "ord_status": c.get("ord_status"),
        "client": {"id": str(c["client_id"]), "name": c.get("client_name"), "inn": c.get("client_inn")},
        "contractor": {"id": str(c["contractor_id"]), "name": c.get("contractor_name"), "inn": c.get("contractor_inn")},
        "parent_contract_id": str(c["parent_contract_id"]) if c.get("parent_contract_id") else None,
    }


async def load_chain(conn, campaign: dict) -> dict[str, Any]:
    """Сводка цепочки для окна «Данные в ОРД» и для регистрации креативов."""
    from ord.platform import get_self_org

    counterparty = await _fetch_counterparty(conn, campaign.get("counterparty_id"))
    initial = await _fetch_contract(conn, campaign.get("initial_contract_id"))
    executive = await _fetch_contract(conn, campaign.get("executive_contract_id"))
    self_org = await get_self_org(conn)
    mode = campaign.get("ord_mode")
    problems = validate_chain(mode, counterparty, initial, executive, self_org)
    confirmed_at = campaign.get("ord_confirmed_at")
    reg = registration_contract(campaign, initial, executive)
    return {
        "campaign_id": str(campaign["id"]) if campaign.get("id") else None,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode) if mode else None,
        "advertiser": _cp_view(counterparty),
        "initial_contract": _contract_view(initial),
        "executive_contract": _contract_view(executive),
        "self_org": _cp_view(self_org),
        "registration_contract_id": _id(reg),
        "problems": problems,
        "problem_labels": [PROBLEM_LABELS.get(p, p) for p in problems],
        "ready": not problems,
        "confirmed_at": confirmed_at.isoformat() if confirmed_at else None,
        "confirmed": confirmed_at is not None,
        "_objects": {"counterparty": counterparty, "initial": initial, "executive": executive, "self_org": self_org},
    }


def public_chain(chain: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in chain.items() if not k.startswith("_")}


def chain_changed(current: dict, updates: dict) -> bool:
    for f in CHAIN_FIELDS:
        if f in updates and str(updates[f] or "") != str(current.get(f) or ""):
            return True
    return False


def assert_not_locked(current: dict, updates: dict) -> None:
    """Изменение цепочки после подтверждения → 409 ord_locked (юр. данные,
    на них выданы erid). Пользовательское решение B13a: другой клиент =
    новая кампания."""
    if current.get("ord_confirmed_at") and chain_changed(current, updates):
        raise HTTPException(
            status_code=409,
            detail={"code": "ord_locked",
                    "message": "Данные ОРД подтверждены и заморожены. Для другого рекламодателя или договора создайте новую кампанию."},
        )


async def confirm_chain(conn, campaign: dict) -> dict[str, Any]:
    """Подтверждение цепочки («Всё верно»). Идемпотентно."""
    chain = await load_chain(conn, campaign)
    if chain["confirmed"]:
        return chain
    if not chain["ready"]:
        raise HTTPException(
            status_code=422,
            detail={"code": "ord_chain_required", "message": "Цепочка ОРД не готова к подтверждению.",
                    "problems": chain["problems"], "problem_labels": chain["problem_labels"]},
        )
    row = await conn.fetchrow(
        """UPDATE campaigns SET ord_confirmed_at = COALESCE(ord_confirmed_at, now()), updated_at = now()
            WHERE id = $1 RETURNING ord_confirmed_at""",
        campaign["id"],
    )
    chain["confirmed_at"] = row["ord_confirmed_at"].isoformat()
    chain["confirmed"] = True
    return chain
