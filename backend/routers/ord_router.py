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

router = APIRouter(prefix="/ord", tags=["ord"])


# ---------------------------------------------------------------- schemas --
class CounterpartyIn(BaseModel):
    name: str
    inn: str = Field(min_length=5, max_length=30)
    legal_type: str = "ul"           # ul | ip | fl | foreign
    role: str = "advertiser"         # advertiser | agency | publisher | ors
    advertiser_id: Optional[str] = None


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
    contract_id: str


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
        """INSERT INTO counterparties (user_id, advertiser_id, name, inn, legal_type, role)
           VALUES ($1,$2,$3,$4,$5,$6)
           ON CONFLICT (user_id, inn, role) DO UPDATE SET name = EXCLUDED.name, updated_at = now()
           RETURNING *""",
        user["id"], body.advertiser_id, body.name, body.inn, body.legal_type, body.role,
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
    adapter = get_ord_adapter()
    try:
        out = await adapter.register_counterparty(
            {"name": cp["name"], "inn": cp["inn"], "legal_type": cp["legal_type"], "role": cp["role"]}
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
    adapter = get_ord_adapter()
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
    """Регистрирует креатив в ОРД, получает erid, денормализует его в creatives.erid."""
    import json as _json

    pool = await get_pool()
    cr = await pool.fetchrow(
        "SELECT * FROM creatives WHERE id = $1 AND user_id = $2", creative_id, user["id"]
    )
    if not cr:
        raise HTTPException(404, "Креатив не найден")
    existing = await pool.fetchrow("SELECT * FROM ord_creatives WHERE creative_id = $1", creative_id)
    if existing and existing["status"] == "registered":
        return _row(existing)  # идемпотентно
    contract = await pool.fetchrow(
        "SELECT * FROM ord_contracts WHERE id = $1 AND user_id = $2", body.contract_id, user["id"]
    )
    if not contract or contract["ord_status"] != "registered":
        raise HTTPException(409, "Договор должен быть зарегистрирован в ОРД до регистрации креатива")

    payload = {
        "contract_external_id": contract["ord_external_id"],
        "type": cr["type"],
        "name": cr["name"],
        "click_url": cr.get("click_url") if isinstance(cr, dict) else cr["click_url"],
        "description": cr["native_description"] or cr["name"],
    }
    adapter = get_ord_adapter()
    try:
        out = await adapter.register_creative(payload)
    except OrdError as e:
        await pool.execute(
            """INSERT INTO ord_creatives (creative_id, contract_id, status, payload, error)
               VALUES ($1,$2,'failed',$3,$4)
               ON CONFLICT (creative_id) DO UPDATE
                 SET status='failed', error=EXCLUDED.error, payload=EXCLUDED.payload, updated_at=now()""",
            creative_id, body.contract_id, _json.dumps(payload, ensure_ascii=False), str(e),
        )
        raise HTTPException(502, f"ОРД: {e}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO ord_creatives
                     (creative_id, contract_id, erid, ord_provider, ord_external_id,
                      status, payload, registered_at)
                   VALUES ($1,$2,$3,$4,$5,'registered',$6, now())
                   ON CONFLICT (creative_id) DO UPDATE
                     SET erid=EXCLUDED.erid, ord_external_id=EXCLUDED.ord_external_id,
                         contract_id=EXCLUDED.contract_id, status='registered',
                         error=NULL, registered_at=now(), updated_at=now()
                   RETURNING *""",
                creative_id, body.contract_id, out["erid"], adapter.provider,
                out.get("external_id"), _json.dumps(payload, ensure_ascii=False),
            )
            await conn.execute(
                "UPDATE creatives SET erid = $2, updated_at = now() WHERE id = $1",
                creative_id, out["erid"],
            )
    return _row(row)


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
