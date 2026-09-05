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
from rbac import require_platform_write
from db import get_pool
from ord import OrdError, get_ord_adapter
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
    rows = await pool.fetch(
        "SELECT * FROM counterparties WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
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
    row = await pool.fetchrow(
        """INSERT INTO counterparties (user_id, advertiser_id, name, inn, ogrn, legal_type, role, address)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
           ON CONFLICT (user_id, inn, role) DO UPDATE
             SET name = EXCLUDED.name, ogrn = COALESCE(EXCLUDED.ogrn, counterparties.ogrn), address = COALESCE(EXCLUDED.address, counterparties.address), updated_at = now()
           RETURNING *""",
        user["id"], body.advertiser_id, body.name, body.inn, body.ogrn, body.legal_type, body.role, body.address,
    )
    return _row(row)


@router.post("/counterparties/{cp_id}/register")
async def register_counterparty(cp_id: str, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    cp = await pool.fetchrow(
        "SELECT * FROM counterparties WHERE id = $1 AND user_id = $2", cp_id, user["id"]
    )
    if not cp:
        raise HTTPException(404, "Контрагент не найден")
    if cp["ord_status"] == "registered":
        return _row(cp)  # идемпотентно
    adapter = await get_ord_adapter()
    try:
        out = await adapter.register_counterparty(
            {"name": cp["name"], "inn": cp["inn"], "ogrn": cp.get("ogrn"), "legal_type": cp["legal_type"],
             "role": cp["role"], "address": cp["address"]}
        )
        row = await pool.fetchrow(
            """UPDATE counterparties SET ord_external_id=$3, ord_status='registered',
               ord_error=NULL, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *""",
            cp_id, user["id"], out["external_id"],
        )
        return _row(row)
    except OrdError as e:
        await pool.execute(
            "UPDATE counterparties SET ord_status='failed', ord_error=$3, updated_at=now() WHERE id=$1 AND user_id=$2",
            cp_id, user["id"], str(e),
        )
        raise HTTPException(502, f"ОРД: {e}")


# ---------------------------------------------------------------- contracts --
@router.get("/contracts")
async def list_contracts(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT c.*, cl.name AS client_name, ct.name AS contractor_name
           FROM ord_contracts c
           JOIN counterparties cl ON cl.id = c.client_id
           JOIN counterparties ct ON ct.id = c.contractor_id
           WHERE c.user_id = $1 ORDER BY c.created_at DESC""",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("/contracts", status_code=201)
async def create_contract(body: ContractIn, user: dict = Depends(require_platform_write)):
    from datetime import date

    pool = await get_pool()
    for cp_id in (body.client_id, body.contractor_id):
        ok = await pool.fetchval(
            "SELECT 1 FROM counterparties WHERE id = $1 AND user_id = $2", cp_id, user["id"]
        )
        if not ok:
            raise HTTPException(400, f"Контрагент {cp_id} не найден")
    row = await pool.fetchrow(
        """INSERT INTO ord_contracts (user_id, client_id, contractor_id, contract_type,
             subject_type, number, signed_at, amount, is_vat, parent_contract_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *""",
        user["id"], body.client_id, body.contractor_id, body.contract_type,
        body.subject_type, body.number, date.fromisoformat(body.signed_at),
        body.amount, body.is_vat, body.parent_contract_id,
    )
    return _row(row)


@router.post("/contracts/{contract_id}/register")
async def register_contract(contract_id: str, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    c = await pool.fetchrow(
        "SELECT * FROM ord_contracts WHERE id = $1 AND user_id = $2", contract_id, user["id"]
    )
    if not c:
        raise HTTPException(404, "Договор не найден")
    if c["ord_status"] == "registered":
        return _row(c)
    # обе стороны должны быть зарегистрированы в ОРД
    for side in ("client_id", "contractor_id"):
        st = await pool.fetchval("SELECT ord_status FROM counterparties WHERE id = $1", c[side])
        if st != "registered":
            raise HTTPException(409, "Сначала зарегистрируйте обоих контрагентов в ОРД")
    adapter = await get_ord_adapter()
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
            contract_id, user["id"], out["external_id"],
        )
        return _row(row)
    except OrdError as e:
        await pool.execute(
            "UPDATE ord_contracts SET ord_status='failed', ord_error=$3, updated_at=now() WHERE id=$1 AND user_id=$2",
            contract_id, user["id"], str(e),
        )
        raise HTTPException(502, f"ОРД: {e}")


# ---------------------------------------------------------------- creatives --
@router.post("/creatives/{creative_id}/register")
async def register_creative(creative_id: str, body: CreativeRegisterIn, user: dict = Depends(require_platform_write)):
    """Ручная регистрация креатива в ОРД (страница «Маркировка»). Тот же код,
    что авторегистрация при approve — ord.service.register_creative."""
    pool = await get_pool()
    cr = await pool.fetchrow(
        "SELECT * FROM creatives WHERE id = $1 AND user_id = $2", creative_id, user["id"]
    )
    if not cr:
        raise HTTPException(404, "Креатив не найден")
    if body.contract_id:
        owns = await pool.fetchval(
            "SELECT 1 FROM ord_contracts WHERE id = $1 AND user_id = $2", body.contract_id, user["id"]
        )
        if not owns:
            raise HTTPException(404, "Договор не найден")
    result = await ord_register_creative(pool, dict(cr), contract_id=body.contract_id)
    if result["outcome"] == "awaiting_contract":
        raise HTTPException(409, result.get("error") or "Договор должен быть зарегистрирован в ОРД до регистрации креатива")
    if result["outcome"] == "failed":
        raise HTTPException(502, f"ОРД: {result.get('error')}")
    return {k: v for k, v in result.items() if k != "outcome"}


@router.get("/creatives")
async def list_ord_creatives(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT oc.*, c.name AS creative_name, c.campaign_id
           FROM ord_creatives oc JOIN creatives c ON c.id = oc.creative_id
           WHERE c.user_id = $1 ORDER BY oc.created_at DESC""",
        user["id"],
    )
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ exports --
@router.get("/exports")
async def list_exports(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch("SELECT * FROM ord_export_log ORDER BY started_at DESC LIMIT 50")
    return [dict(r) for r in rows]
