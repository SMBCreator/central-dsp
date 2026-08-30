import json

from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from rbac import require_campaign_editor
from db import get_pool
from schemas import LineItemCreativesIn, LineItemIn, LineItemPatch

router = APIRouter(prefix="/line-items", tags=["line-items"])


def _serialize(row) -> dict:
    d = dict(row)
    for k in ("id", "campaign_id", "user_id"):
        d[k] = str(d[k])
    for k in ("bid_price", "max_bid", "daily_budget", "spent_today"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    if isinstance(d.get("targeting"), str):
        d["targeting"] = json.loads(d["targeting"])
    return d


@router.get("")
async def list_line_items(campaign_id: str | None = None, user: dict = Depends(current_user)):
    pool = await get_pool()
    if campaign_id:
        rows = await pool.fetch(
            "SELECT * FROM line_items WHERE user_id = $1 AND campaign_id = $2 ORDER BY created_at DESC",
            user["id"], campaign_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM line_items WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
        )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_line_item(body: LineItemIn, user: dict = Depends(require_campaign_editor)):
    pool = await get_pool()
    camp = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", body.campaign_id, user["id"]
    )
    if not camp:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    row = await pool.fetchrow(
        """
        INSERT INTO line_items (campaign_id, user_id, name, bid_type, bid_price, max_bid, daily_budget, targeting)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) RETURNING *
        """,
        body.campaign_id, user["id"], body.name, body.bid_type, body.bid_price,
        body.max_bid, body.daily_budget, json.dumps(body.targeting),
    )
    return _serialize(row)


@router.patch("/{line_item_id}")
async def update_line_item(line_item_id: str, body: LineItemPatch, user: dict = Depends(require_campaign_editor)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        if k == "targeting":
            sets.append(f"{k} = ${i}::jsonb")
            values.append(json.dumps(v))
        else:
            sets.append(f"{k} = ${i}")
            values.append(v)
    row = await pool.fetchrow(
        f"UPDATE line_items SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        line_item_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    return _serialize(row)


@router.delete("/{line_item_id}", status_code=204)
async def delete_line_item(line_item_id: str, user: dict = Depends(require_campaign_editor)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM line_items WHERE id = $1 AND user_id = $2", line_item_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Позиция не найдена")


@router.put("/{line_item_id}/creatives")
async def set_line_item_creatives(
    line_item_id: str, body: LineItemCreativesIn, user: dict = Depends(require_campaign_editor)
):
    """G9: привязка креативов к line item.

    Пустой список creative_ids = «все креативы кампании» (снимаем привязку у всех).
    Непустой список = только эти креативы крутятся в данном line item.
    """
    pool = await get_pool()
    li = await pool.fetchrow(
        "SELECT id, campaign_id FROM line_items WHERE id = $1 AND user_id = $2",
        line_item_id, user["id"],
    )
    if not li:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    campaign_id = li["campaign_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Снимаем привязку с креативов кампании, которые больше не выбраны
            await conn.execute(
                """UPDATE creatives SET line_item_id = NULL, updated_at = now()
                   WHERE campaign_id = $1 AND user_id = $2 AND line_item_id = $3
                     AND NOT (id = ANY($4::uuid[]))""",
                campaign_id, user["id"], line_item_id, body.creative_ids,
            )
            if body.creative_ids:
                result = await conn.execute(
                    """UPDATE creatives SET line_item_id = $3, updated_at = now()
                       WHERE campaign_id = $1 AND user_id = $2 AND id = ANY($4::uuid[])""",
                    campaign_id, user["id"], line_item_id, body.creative_ids,
                )
                updated = int(result.split(" ")[-1] or 0)
                if updated != len(set(body.creative_ids)):
                    raise HTTPException(
                        status_code=400,
                        detail="Некоторые креативы не принадлежат этой кампании",
                    )
    rows = await pool.fetch(
        "SELECT id FROM creatives WHERE line_item_id = $1 AND user_id = $2",
        line_item_id, user["id"],
    )
    return {"line_item_id": str(line_item_id), "creative_ids": [str(r["id"]) for r in rows]}
