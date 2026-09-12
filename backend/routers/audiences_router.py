"""Audience segments CRUD for retargeting."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import current_user
from rbac import require_campaign_write

router = APIRouter(prefix="/audiences", tags=["audiences"])


class SegmentIn(BaseModel):
    pixel_id: str
    name: str = Field(min_length=1, max_length=200)
    rule_type: str = Field(pattern="^(all_visitors|url_contains|event|goal)$")
    rule_value: str | None = Field(default=None, max_length=500)
    ttl_days: int = Field(default=30, ge=1, le=180)


class SegmentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    ttl_days: int | None = Field(default=None, ge=1, le=180)


@router.get("")
async def list_segments(user=Depends(current_user)):
    from db import get_pool
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT s.*, p.name AS pixel_name,
                  (SELECT COUNT(*) FROM audience_members m
                   WHERE m.segment_id = s.id AND m.expires_at > now()) AS members
           FROM audience_segments s
           JOIN pixels p ON p.id = s.pixel_id
           WHERE s.user_id = $1
           ORDER BY s.created_at DESC""",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_segment(body: SegmentIn, user=Depends(require_campaign_write)):
    from db import get_pool
    pool = await get_pool()
    pixel = await pool.fetchrow(
        "SELECT id FROM pixels WHERE id = $1 AND user_id = $2", body.pixel_id, user["id"])
    if not pixel:
        raise HTTPException(status_code=404, detail="Пиксель не найден")
    if body.rule_type in ("url_contains", "event", "goal") and not body.rule_value:
        raise HTTPException(status_code=422, detail="Для этого типа правила нужно значение")
    row = await pool.fetchrow(
        """INSERT INTO audience_segments (user_id, pixel_id, name, rule_type, rule_value, ttl_days)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING *""",
        user["id"], body.pixel_id, body.name, body.rule_type, body.rule_value, body.ttl_days,
    )
    return dict(row)


@router.patch("/{segment_id}")
async def patch_segment(segment_id: uuid.UUID, body: SegmentPatch, user=Depends(require_campaign_write)):
    from db import get_pool
    pool = await get_pool()
    seg = await pool.fetchrow(
        "SELECT id FROM audience_segments WHERE id = $1 AND user_id = $2", segment_id, user["id"])
    if not seg:
        raise HTTPException(status_code=404)
    sets, args, n = [], [], 1
    for col in ("name", "is_active", "ttl_days"):
        v = getattr(body, col)
        if v is not None:
            sets.append(f"{col} = ${n}")
            args.append(v)
            n += 1
    if not sets:
        return {"ok": True}
    args.extend([segment_id, user["id"]])
    row = await pool.fetchrow(
        f"UPDATE audience_segments SET {', '.join(sets)} WHERE id = ${n} AND user_id = ${n+1} RETURNING *",
        *args,
    )
    return dict(row)


@router.delete("/{segment_id}", status_code=204)
async def delete_segment(segment_id: uuid.UUID, user=Depends(require_campaign_write)):
    from db import get_pool
    pool = await get_pool()
    deleted = await pool.fetchval(
        "DELETE FROM audience_segments WHERE id = $1 AND user_id = $2 RETURNING id",
        segment_id, user["id"])
    if not deleted:
        raise HTTPException(status_code=404)
