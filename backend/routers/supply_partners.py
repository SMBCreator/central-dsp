import json
import secrets

from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from db import get_pool
from schemas import SupplyPartnerIn, SupplyPartnerPatch

router = APIRouter(prefix="/supply-partners", tags=["supply-partners"])


def _serialize(row) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    d["bid_floor"] = float(d["bid_floor"])
    if isinstance(d.get("allowed_ad_types"), str):
        d["allowed_ad_types"] = json.loads(d["allowed_ad_types"])
    return d


@router.get("")
async def list_partners(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM supply_partners WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
    )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_partner(body: SupplyPartnerIn, user: dict = Depends(current_user)):
    pool = await get_pool()
    endpoint_key = secrets.token_urlsafe(16)
    row = await pool.fetchrow(
        """
        INSERT INTO supply_partners (user_id, name, endpoint_key, auction_type, bid_floor, qps_limit, allowed_ad_types, notes)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8) RETURNING *
        """,
        user["id"], body.name, endpoint_key, body.auction_type, body.bid_floor,
        body.qps_limit, json.dumps(body.allowed_ad_types), body.notes,
    )
    return _serialize(row)


@router.patch("/{partner_id}")
async def update_partner(partner_id: str, body: SupplyPartnerPatch, user: dict = Depends(current_user)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        if k == "allowed_ad_types":
            sets.append(f"{k} = ${i}::jsonb")
            values.append(json.dumps(v))
        else:
            sets.append(f"{k} = ${i}")
            values.append(v)
    row = await pool.fetchrow(
        f"UPDATE supply_partners SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        partner_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    return _serialize(row)


@router.delete("/{partner_id}", status_code=204)
async def delete_partner(partner_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM supply_partners WHERE id = $1 AND user_id = $2", partner_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
