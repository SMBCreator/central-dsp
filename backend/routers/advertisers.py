from fastapi import APIRouter, Depends, HTTPException

from ad_market import change_ad_market
from auth import current_user
from rbac import assert_in_scope, require_campaign_write, scope_for
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
    scope = await scope_for(user)
    rows = await pool.fetch(
        f"SELECT * FROM advertisers WHERE {scope.where('user_id', 1)} ORDER BY created_at DESC", *scope.params
    )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_advertiser(body: AdvertiserIn, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    # Balance always starts at 0 (column default); top-ups go through /billing.
    row = await pool.fetchrow(
        """
        INSERT INTO advertisers (user_id, name, domain, category, currency, notify_emails, ad_market)
        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *
        """,
        user["id"], body.name, body.domain, body.category, body.currency, body.notify_emails, body.ad_market,
    )
    return _serialize(row)


@router.get("/{advertiser_id}")
async def get_advertiser(advertiser_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    row = await assert_in_scope(pool, "advertisers", advertiser_id, await scope_for(user), "Рекламодатель не найден")
    return _serialize(row)


@router.patch("/{advertiser_id}")
async def update_advertiser(advertiser_id: str, body: AdvertiserPatch, user: dict = Depends(require_campaign_write)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    scope = await scope_for(user)
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=4):
        sets.append(f"{k} = ${i}")
        values.append(v)
    async with pool.acquire() as conn, conn.transaction():
        if "ad_market" in updates:
            current = await conn.fetchrow(
                f"SELECT id, ad_market FROM advertisers WHERE id = $1 AND {scope.where('user_id', 2)} FOR UPDATE",
                advertiser_id, *scope.params,
            )
            if not current:
                raise HTTPException(status_code=404, detail="Рекламодатель не найден")
            await change_ad_market(conn, dict(current), updates["ad_market"], user)
        row = await conn.fetchrow(
            f"UPDATE advertisers SET {', '.join(sets)}, updated_at = now() "
            f"WHERE id = $1 AND {scope.where('user_id', 2)} RETURNING *",
            advertiser_id, *scope.params, *values,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    return _serialize(row)


@router.delete("/{advertiser_id}", status_code=204)
async def delete_advertiser(advertiser_id: str, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    scope = await scope_for(user)
    result = await pool.execute(
        f"DELETE FROM advertisers WHERE id = $1 AND {scope.where('user_id', 2)}", advertiser_id, *scope.params
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
