"""Creative moderation queue (admin only)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_pool
from notify import notify_user
from rbac import require_admin

router = APIRouter(prefix="/moderation", tags=["moderation"])


class RejectIn(BaseModel):
    comment: str = ""


def _ser(row) -> dict:
    d = dict(row)
    for k in ("id", "campaign_id", "user_id", "line_item_id", "moderated_by", "counterparty_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    return d


@router.get("/queue")
async def moderation_queue(status: str = "pending", user: dict = Depends(require_admin)):
    if status not in ("pending", "approved", "rejected"):
        raise HTTPException(422, "status: pending|approved|rejected")
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT c.*, camp.name AS campaign_name, u.email AS owner_email,
               cp.name AS counterparty_name, cp.inn AS counterparty_inn,
               cp.ogrn AS counterparty_ogrn, cp.address AS counterparty_address
        FROM creatives c
        JOIN campaigns camp ON camp.id = c.campaign_id
        JOIN users u ON u.id = c.user_id
        LEFT JOIN counterparties cp ON cp.id = c.counterparty_id
        WHERE c.status = $1
        ORDER BY c.created_at ASC
        """,
        status,
    )
    return [_ser(r) for r in rows]


@router.post("/{creative_id}/approve")
async def approve_creative(creative_id: str, user: dict = Depends(require_admin)):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE creatives SET status = 'approved', moderation_comment = NULL,
               moderated_by = $2, moderated_at = now(), updated_at = now()
        WHERE id = $1 AND status = 'pending' RETURNING *
        """,
        creative_id, user["id"],
    )
    if not row:
        raise HTTPException(404, "Креатив не найден или уже промодерирован")
    await notify_user(
        row["user_id"], "moderation_approved",
        f"Креатив «{row['name']}» одобрен",
        "Креатив прошёл модерацию и будет участвовать в показах.",
        f"/creatives?campaign_id={row['campaign_id']}",
    )
    return _ser(row)


@router.post("/{creative_id}/reject")
async def reject_creative(creative_id: str, body: RejectIn, user: dict = Depends(require_admin)):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE creatives SET status = 'rejected', moderation_comment = $3,
               moderated_by = $2, moderated_at = now(), updated_at = now()
        WHERE id = $1 AND status = 'pending' RETURNING *
        """,
        creative_id, user["id"], body.comment or "",
    )
    if not row:
        raise HTTPException(404, "Креатив не найден или уже промодерирован")
    await notify_user(
        row["user_id"], "moderation_rejected",
        f"Креатив «{row['name']}» отклонён",
        (body.comment or "Без комментария"),
        f"/creatives?campaign_id={row['campaign_id']}",
    )
    return _ser(row)
