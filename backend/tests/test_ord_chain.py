"""Цепочка ОРД на кампании (B13d, ord_spec §7 D2): чистая валидация и заморозка."""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ord import chain

ADV = {"id": "adv", "name": "ООО Ромашка", "ord_status": "registered", "is_self": False}
AGENCY = {"id": "ag", "name": "Агентство", "ord_status": "registered", "is_self": False}
SELF = {"id": "self", "name": "Central DSP", "ord_status": "registered", "is_self": True}


def _contract(cid, client, contractor, status="registered", parent=None):
    return {"id": cid, "client_id": client["id"], "contractor_id": contractor["id"],
            "ord_status": status, "parent_contract_id": parent}


INITIAL = _contract("ct1", ADV, AGENCY)
EXEC = _contract("ct2", AGENCY, SELF)
EXEC_DIRECT = _contract("ct3", ADV, SELF)


def test_mode_required():
    assert chain.validate_chain(None, ADV, None, None, SELF) == ["mode_missing"]
    assert chain.validate_chain("weird", ADV, None, None, SELF) == ["mode_invalid"]


def test_own_client_full_chain_is_ready():
    assert chain.validate_chain("own_client", ADV, INITIAL, EXEC, SELF) == []


def test_own_client_requires_both_contracts_and_registered_advertiser():
    p = chain.validate_chain("own_client", None, None, None, SELF)
    assert p == ["counterparty_missing", "initial_missing", "executive_missing"]
    p = chain.validate_chain("own_client", {**ADV, "ord_status": "pending"}, INITIAL, EXEC, SELF)
    assert p == ["counterparty_unregistered"]


def test_own_client_contract_parties_must_match():
    wrong_client = _contract("x", AGENCY, AGENCY)
    assert "initial_client_mismatch" in chain.validate_chain("own_client", ADV, wrong_client, EXEC, SELF)
    exec_not_self = _contract("y", AGENCY, AGENCY)
    assert "executive_contractor_mismatch" in chain.validate_chain("own_client", ADV, INITIAL, exec_not_self, SELF)
    # заказчик исполнительского должен быть исполнителем изначального
    assert "executive_client_mismatch" in chain.validate_chain("own_client", ADV, INITIAL, EXEC_DIRECT, SELF)


def test_unregistered_contracts_block():
    p = chain.validate_chain("own_client", ADV, {**INITIAL, "ord_status": "pending"},
                             {**EXEC, "ord_status": "error"}, SELF)
    assert p == ["initial_unregistered", "executive_unregistered"]


def test_self_mode_uses_only_executive_with_advertiser_as_client():
    assert chain.validate_chain("self", ADV, None, EXEC_DIRECT, SELF) == []
    p = chain.validate_chain("self", ADV, INITIAL, EXEC, SELF)
    assert "initial_not_allowed" in p and "executive_client_not_advertiser" in p


def test_partner_client_allows_intermediary_via_parent_contract():
    exec_via = _contract("ct4", {"id": "other_agency"}, SELF, parent="mid")
    assert chain.validate_chain("partner_client", ADV, INITIAL, exec_via, SELF) == []
    # без посреднического договора — та же проверка, что и own_client
    exec_no_parent = _contract("ct5", {"id": "other_agency"}, SELF)
    assert "executive_client_mismatch" in chain.validate_chain("partner_client", ADV, INITIAL, exec_no_parent, SELF)


def test_self_org_required_and_self_promotion_not_supported():
    assert "self_org_missing" in chain.validate_chain("own_client", ADV, INITIAL, EXEC, None)
    assert chain.validate_chain("own_client", SELF, INITIAL, EXEC, SELF) == ["self_promotion_not_supported"]


def test_registration_contract_is_initial_except_self_mode():
    assert chain.registration_contract({"ord_mode": "own_client"}, INITIAL, EXEC) is INITIAL
    assert chain.registration_contract({"ord_mode": "partner_client"}, INITIAL, EXEC) is INITIAL
    assert chain.registration_contract({"ord_mode": "self"}, None, EXEC_DIRECT) is EXEC_DIRECT


def test_locked_chain_rejects_changes_but_allows_same_values_and_other_fields():
    cur = {"ord_confirmed_at": "2026-09-01", "ord_mode": "own_client", "counterparty_id": "adv",
           "initial_contract_id": "ct1", "executive_contract_id": "ct2"}
    chain.assert_not_locked(cur, {"name": "new", "daily_budget": 5})
    chain.assert_not_locked(cur, {"counterparty_id": "adv", "ord_mode": "own_client"})
    with pytest.raises(HTTPException) as e:
        chain.assert_not_locked(cur, {"counterparty_id": "other"})
    assert e.value.status_code == 409 and e.value.detail["code"] == "ord_locked"
    # до подтверждения менять можно
    chain.assert_not_locked({**cur, "ord_confirmed_at": None}, {"counterparty_id": "other"})


def test_every_problem_code_has_label():
    codes = {
        "mode_missing", "mode_invalid", "counterparty_missing", "counterparty_unregistered",
        "self_promotion_not_supported", "self_org_missing", "initial_missing", "initial_not_allowed",
        "initial_client_mismatch", "initial_unregistered", "executive_missing",
        "executive_contractor_mismatch", "executive_client_mismatch", "executive_client_not_advertiser",
        "executive_unregistered",
    }
    assert codes <= set(chain.PROBLEM_LABELS)
