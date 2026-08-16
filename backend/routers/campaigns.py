from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from db import get_pool
from schemas import CampaignIn, CampaignPatch

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

_NUMERIC = {"daily_budget", "total_budget", "spent_today", "spent_total"}
_DATES = {"start_date", "end_date"}


def _serialize(row) -> dict:
    d = dict(row)
    for k in ("id", "advertiser_id", "user_id"):
        d[k] = str(d[k])
    for k in _NUMERIC:
        if k in d and d[k] is not None:
            d[k] = float(d[k])
    for k in _DATES:
        if d.get(k):
            d[k] = d[k].isoformat()
    return d


def _parse_date(val: str | None) -> date | None:
    return date.fromisoformat(val) if val else None


@router.get("")
async def list_campaigns(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT c.*, a.name AS advertiser_name
        FROM campaigns c JOIN advertisers a ON a.id = c.advertiser_id
        WHERE c.user_id = $1 ORDER BY c.created_at DESC
        """,
        user["id"],
    )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_campaign(body: CampaignIn, user: dict = Depends(current_user)):
    pool = await get_pool()
    adv = await pool.fetchval(
        "SELECT 1 FROM advertisers WHERE id = $1 AND user_id = $2", body.advertiser_id, user["id"]
    )
    if not adv:
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    row = await pool.fetchrow(
        """
        INSERT INTO campaigns (advertiser_id, user_id, name, objective, daily_budget, total_budget,
                               start_date, end_date, pacing, frequency_cap_impressions, frequency_cap_period_hours)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING *
        """,
        body.advertiser_id, user["id"], body.name, body.objective, body.daily_budget,
        body.total_budget, _parse_date(body.start_date), _parse_date(body.end_date),
        body.pacing, body.frequency_cap_impressions, body.frequency_cap_period_hours,
    )
    return _serialize(row)


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return _serialize(row)


@router.patch("/{campaign_id}")
async def update_campaign(campaign_id: str, body: CampaignPatch, user: dict = Depends(current_user)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    for k in _DATES:
        if k in updates:
            updates[k] = _parse_date(updates[k])
    pool = await get_pool()
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        sets.append(f"{k} = ${i}")
        values.append(v)
    row = await pool.fetchrow(
        f"UPDATE campaigns SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        campaign_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return _serialize(row)


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Кампания не найдена")
