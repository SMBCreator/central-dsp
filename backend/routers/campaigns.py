from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from rbac import can_see_all, require_campaign_write
from db import get_pool
from schemas import CampaignIn, CampaignPatch

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

_NUMERIC = {"daily_budget", "total_budget", "spent_today", "spent_total"}
_DATES = {"start_date", "end_date"}


def _serialize(row) -> dict:
    d = dict(row)
    for k in ("id", "advertiser_id", "user_id"):
        d[k] = str(d[k])
    for k in ("group_id", "pixel_id", "goal_id"):
        if d.get(k) is not None:
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
    if can_see_all(user):
        rows = await pool.fetch(
            """
            SELECT c.*, a.name AS advertiser_name
            FROM campaigns c JOIN advertisers a ON a.id = c.advertiser_id
            ORDER BY c.created_at DESC
            """
        )
    else:
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
async def create_campaign(body: CampaignIn, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    adv = await pool.fetchval(
        "SELECT 1 FROM advertisers WHERE id = $1 AND user_id = $2", body.advertiser_id, user["id"]
    )
    if not adv:
        raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    row = await pool.fetchrow(
        """
        INSERT INTO campaigns (advertiser_id, user_id, name, objective, daily_budget, total_budget,
                               start_date, end_date, pacing, frequency_cap_impressions, frequency_cap_period_hours,
                               group_id, kktu_code, kktu_label)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) RETURNING *
        """,
        body.advertiser_id, user["id"], body.name, body.objective, body.daily_budget,
        body.total_budget, _parse_date(body.start_date), _parse_date(body.end_date),
        body.pacing, body.frequency_cap_impressions, body.frequency_cap_period_hours, body.group_id,
        body.kktu_code, body.kktu_label,
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
async def update_campaign(campaign_id: str, body: CampaignPatch, user: dict = Depends(require_campaign_write)):
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
    # Launch guard (ФЗ-347): кампания не активируется, пока у всех её
    # креативов нет erid (маркировка через ОРД). ORD_LAUNCH_GUARD=off — только dev.
    import os as _os
    if updates.get("status") == "active" and _os.getenv("ORD_LAUNCH_GUARD", "on") != "off":
        # Гуард действует только для РФ-трафика: Россия явно выбрана в гео
        # хотя бы одного line item, или гео не ограничено (весь мир включает РФ).
        targets_ru = await pool.fetchval(
            """SELECT bool_or(
                     COALESCE(jsonb_array_length(targeting->'geo_countries'), 0) = 0
                     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(targeting->'geo_countries') g
                                WHERE upper(g) IN ('RU','RUS'))
                   )
               FROM line_items WHERE campaign_id = $1""",
            campaign_id,
        )
        unmarked = 0
        if targets_ru is None or targets_ru:  # нет line items = считаем, что РФ возможна
            unmarked = await pool.fetchval(
                """SELECT count(*) FROM creatives
                   WHERE campaign_id = $1 AND (erid IS NULL OR erid = '')""",
                campaign_id,
            )
        if unmarked:
            raise HTTPException(
                status_code=409,
                detail=f"Нельзя активировать: {unmarked} креатив(ов) без erid. "
                       "Зарегистрируйте креативы в ОРД (раздел Маркировка).",
            )
    row = await pool.fetchrow(
        f"UPDATE campaigns SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        campaign_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return _serialize(row)


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Кампания не найдена")


@router.get("/{campaign_id}/blocks")
async def list_optimizer_blocks(campaign_id: str, user: dict = Depends(current_user)):
    """Traffic sources blocked by the buying optimizer for this campaign."""
    pool = await get_pool()
    owned = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    rows = await pool.fetch(
        """SELECT b.*, sp.name AS partner_name
           FROM optimizer_blocks b
           LEFT JOIN supply_partners sp ON sp.id::text = b.block_value AND b.block_type = 'supply_partner'
           WHERE b.campaign_id = $1 ORDER BY b.created_at DESC""",
        campaign_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["id"], d["campaign_id"] = str(d["id"]), str(d["campaign_id"])
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


@router.delete("/{campaign_id}/blocks/{block_id}", status_code=204)
async def remove_optimizer_block(campaign_id: str, block_id: str, user: dict = Depends(require_campaign_write)):
    """Manually unblock a source (the optimizer will not re-add it unless metrics degrade again)."""
    pool = await get_pool()
    deleted = await pool.fetchval(
        """DELETE FROM optimizer_blocks b USING campaigns c
           WHERE b.id = $1 AND b.campaign_id = $2 AND c.id = b.campaign_id AND c.user_id = $3
           RETURNING b.id""",
        block_id, campaign_id, user["id"],
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Блок не найден")
