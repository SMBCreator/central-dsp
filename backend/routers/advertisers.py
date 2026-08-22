from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from rbac import can_see_all, require_campaign_write
from db import get_pool
from schemas import AdvertiserIn, AdvertiserPatch

router = APIRouter(prefix="/advertisers", tags=["advertisers"])


def _serialize(row) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    d["balance"] = float(d["balance"])
    return d


@router.get("")
async def list_advertisers(user: dict = Depends(current_user)):
    pool = await get_pool()
    if can_see_all(user):
        rows = await pool.fetch("SELECT * FROM advertisers ORDER BY created_at DESC")
    else:
        rows = await pool.fetch(
            "SELECT * FROM advertisers WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
        )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_advertiser(body: AdvertiserIn, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO advertisers (user_id, name, domain, category, balance, currency)
        VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
        """,
        user["id"], body.name, body.domain, body.category, body.balance, body.currency,
    )
    return _serialize(row)


@router.get("/{advertiser_id}")
async def get_advertiser(advertiser_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM advertisers WHERE id = $1 AND user_id = $2", advertiser_id, user["id"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    return _serialize(row)


@router.patch("/{advertiser_id}")
async def update_advertiser(advertiser_id: str, body: AdvertiserPatch, user: dict = Depends(require_campaign_write)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        sets.append(f"{k} = ${i}")
        values.append(v)
    row = await pool.fetchrow(
        f"UPDATE advertisers SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        advertiser_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    return _serialize(row)


@router.delete("/{advertiser_id}", status_code=204)
async def delete_advertiser(advertiser_id: str, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM advertisers WHERE id = $1 AND user_id = $2", advertiser_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
