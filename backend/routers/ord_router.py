"""ОРД-модуль: контрагенты, договоры, регистрация креативов (erid), выгрузки.

Все мутации идут через get_ord_adapter(): с ключом ORD_OZON_API_KEY — реальный
Ozon-клиент, без ключа — mock (dev). Статусы храним в БД, повторная
регистрация уже registered-сущности не выполняется (идемпотентность).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import current_user
from rbac import assert_in_scope, assert_owned_by, require_platform_write, scope_for
from db import get_pool
from ord import OrdError, OrdNotConfigured, get_ord_adapter
from ord.service import register_creative as ord_register_creative

router = APIRouter(prefix="/ord", tags=["ord"])


# ---------------------------------------------------------------- schemas --
class CounterpartyIn(BaseModel):
    name: str
    inn: str = Field(min_length=5, max_length=30)
    ogrn: Optional[str] = Field(default=None, max_length=20)
    legal_type: str = "ul"           # ul | ip | fl | foreign
    role: str = "advertiser"         # advertiser | agency | publisher | ors
    advertiser_id: Optional[str] = None
    address: Optional[str] = Field(default=None, max_length=500)  # местонахождение (юрадрес)


class ContractIn(BaseModel):
    client_id: str
    contractor_id: str
    contract_type: str = "service"
    subject_type: str = "distribution"
    number: str
    signed_at: str                   # YYYY-MM-DD
    amount: Optional[float] = None
    is_vat: bool = True
    parent_contract_id: Optional[str] = None


class CreativeRegisterIn(BaseModel):
    # Пусто → договор подбирается по контрагенту креатива (ord.service.resolve_contract)
    contract_id: Optional[str] = None


def _row(r) -> dict:
    return dict(r) if r else None


# ----------------------------------------------------------- counterparties --
@router.get("/counterparties")
async def list_counterparties(user: dict = Depends(current_user)):
    pool = await get_pool()
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"SELECT * FROM counterparties WHERE {scope.where('user_id', 1)} ORDER BY created_at DESC", *scope.params
    )
    return [dict(r) for r in rows]


@router.post("/counterparties", status_code=201)
async def create_counterparty(body: CounterpartyIn, user: dict = Depends(require_platform_write)):
    if body.legal_type not in ("ul", "ip", "fl", "foreign"):
        raise HTTPException(400, "legal_type: ul | ip | fl | foreign")
    if body.legal_type in ("ul",) and len(body.inn) != 10 or body.legal_type in ("ip", "fl") and len(body.inn) != 12:
        if body.legal_type != "foreign":
            raise HTTPException(400, "ИНН: 10 цифр для юрлица, 12 для ИП/физлица")
    pool = await get_pool()
    # Контрагент, привязанный к рекламодателю, принадлежит владельцу рекламодателя
    # (admin заводит его клиенту); без привязки — текущему пользователю.
    owner = str(user["id"])
    if body.advertiser_id:
        adv = await assert_in_scope(
            pool, "advertisers", body.advertiser_id, await scope_for(user), "Рекламодатель не найден",
            columns="id, user_id",
        )
        owner = str(adv["user_id"])
    row = await pool.fetchrow(
        """INSERT INTO counterparties (user_id, advertiser_id, name, inn, ogrn, legal_type, role, address)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
           ON CONFLICT (user_id, inn, role) DO UPDATE
             SET name = EXCLUDED.name, ogrn = COALESCE(EXCLUDED.ogrn, counterparties.ogrn), address = COALESCE(EXCLUDED.address, counterparties.address), updated_at = now()
           RETURNING *""",
        owner, body.advertiser_id, body.name, body.inn, body.ogrn, body.legal_type, body.role, body.address,
    )
    return _row(row)


async def _adapter_or_503():
    """Production без ключа ОРД — 503 с инструкцией, не 500 (audit L-01)."""
    try:
        return await get_ord_adapter()
    except OrdNotConfigured as e:
        raise HTTPException(503, str(e))


@router.post("/counterparties/{cp_id}/register")
async def register_counterparty(cp_id: str, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    cp = await assert_in_scope(pool, "counterparties", cp_id, await scope_for(user), "Контрагент не найден")
    owner = str(cp["user_id"])
    if cp["ord_status"] == "registered":
        return _row(cp)  # идемпотентно
    adapter = await _adapter_or_503()
    try:
        out = await adapter.register_counterparty(
            {"name": cp["name"], "inn": cp["inn"], "ogrn": cp.get("ogrn"), "legal_type": cp["legal_type"],
             "role": cp["role"], "address": cp["address"]}
        )
        row = await pool.fetchrow(
            """UPDATE counterparties SET ord_external_id=$3, ord_status='registered',
               ord_error=NULL, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *""",
            cp_id, owner, out["external_id"],
        )
        return _row(row)
    except OrdError as e:
        await pool.execute(
            "UPDATE counterparties SET ord_status='failed', ord_error=$3, updated_at=now() WHERE id=$1 AND user_id=$2",
            cp_id, owner, str(e),
        )
        raise HTTPException(502, f"ОРД: {e}")


# ---------------------------------------------------------------- contracts --
@router.get("/contracts")
async def list_contracts(user: dict = Depends(current_user)):
    pool = await get_pool()
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"""SELECT c.*, cl.name AS client_name, ct.name AS contractor_name
           FROM ord_contracts c
           JOIN counterparties cl ON cl.id = c.client_id
           JOIN counterparties ct ON ct.id = c.contractor_id
           WHERE {scope.where("c.user_id", 1)} ORDER BY c.created_at DESC""",
        *scope.params,
    )
    return [dict(r) for r in rows]


@router.post("/contracts", status_code=201)
async def create_contract(body: ContractIn, user: dict = Depends(require_platform_write)):
    from datetime import date

    pool = await get_pool()
    scope = await scope_for(user)
    # Договор принадлежит владельцу контрагентов; обе стороны — одного владельца,
    # родительский договор — его же (чужой договор в цепочке = чужие данные в фиде ОРД).
    client = await assert_in_scope(pool, "counterparties", body.client_id, scope, "Контрагент не найден",
                                   columns="id, user_id")
    owner = str(client["user_id"])
    await assert_owned_by(pool, "counterparties", body.contractor_id, owner, "Контрагент не найден")
    if body.parent_contract_id:
        await assert_owned_by(pool, "ord_contracts", body.parent_contract_id, owner, "Договор не найден")
    row = await pool.fetchrow(
        """INSERT INTO ord_contracts (user_id, client_id, contractor_id, contract_type,
             subject_type, number, signed_at, amount, is_vat, parent_contract_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *""",
        owner, body.client_id, body.contractor_id, body.contract_type,
        body.subject_type, body.number, date.fromisoformat(body.signed_at),
        body.amount, body.is_vat, body.parent_contract_id,
    )
    return _row(row)


@router.post("/contracts/{contract_id}/register")
async def register_contract(contract_id: str, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    c = await assert_in_scope(pool, "ord_contracts", contract_id, await scope_for(user), "Договор не найден")
    owner = str(c["user_id"])
    if c["ord_status"] == "registered":
        return _row(c)
    # обе стороны должны быть зарегистрированы в ОРД
    for side in ("client_id", "contractor_id"):
        st = await pool.fetchval("SELECT ord_status FROM counterparties WHERE id = $1", c[side])
        if st != "registered":
            raise HTTPException(409, "Сначала зарегистрируйте обоих контрагентов в ОРД")
    adapter = await _adapter_or_503()
    try:
        out = await adapter.register_contract(
            {
                "number": c["number"], "signed_at": str(c["signed_at"]),
                "contract_type": c["contract_type"], "subject_type": c["subject_type"],
                "amount": str(c["amount"] or 0), "is_vat": c["is_vat"],
            }
        )
        row = await pool.fetchrow(
            """UPDATE ord_contracts SET ord_external_id=$3, ord_status='registered',
               ord_error=NULL, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *""",
            contract_id, owner, out["external_id"],
        )
        return _row(row)
    except OrdError as e:
        await pool.execute(
            "UPDATE ord_contracts SET ord_status='failed', ord_error=$3, updated_at=now() WHERE id=$1 AND user_id=$2",
            contract_id, owner, str(e),
        )
        raise HTTPException(502, f"ОРД: {e}")


# ---------------------------------------------------------------- creatives --
@router.post("/creatives/{creative_id}/register")
async def register_creative(creative_id: str, body: CreativeRegisterIn, user: dict = Depends(require_platform_write)):
    """Ручная регистрация креатива в ОРД (страница «Маркировка»). Тот же код,
    что авторегистрация при approve — ord.service.register_creative."""
    pool = await get_pool()
    cr = await assert_in_scope(pool, "creatives", creative_id, await scope_for(user), "Креатив не найден")
    if body.contract_id:
        # Договор — того же владельца, что и креатив (не текущего пользователя).
        await assert_owned_by(pool, "ord_contracts", body.contract_id, cr["user_id"], "Договор не найден")
    result = await ord_register_creative(pool, dict(cr), contract_id=body.contract_id)
    if result["outcome"] == "awaiting_contract":
        raise HTTPException(409, result.get("error") or "Договор должен быть зарегистрирован в ОРД до регистрации креатива")
    if result["outcome"] == "awaiting_kktu":
        raise HTTPException(409, result.get("error") or "Укажите код ККТУ креатива или кампании до регистрации в ОРД")
    if result["outcome"] == "failed":
        raise HTTPException(502, f"ОРД: {result.get('error')}")
    return {k: v for k, v in result.items() if k != "outcome"}


@router.get("/creatives")
async def list_ord_creatives(user: dict = Depends(current_user)):
    pool = await get_pool()
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"""SELECT oc.*, c.name AS creative_name, c.campaign_id
           FROM ord_creatives oc JOIN creatives c ON c.id = oc.creative_id
           WHERE {scope.where("c.user_id", 1)} ORDER BY oc.created_at DESC""",
        *scope.params,
    )
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ exports --
@router.get("/exports")
async def list_exports(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch("SELECT * FROM ord_export_log ORDER BY started_at DESC LIMIT 50")
    return [dict(r) for r in rows]
