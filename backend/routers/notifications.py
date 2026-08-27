from fastapi import APIRouter, Depends

from auth import current_user
from db import get_pool

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _ser(row) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    return d


@router.get("")
async def list_notifications(limit: int = 30, user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM notifications WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
        user["id"], min(limit, 100),
    )
    unread = await pool.fetchval(
        "SELECT count(*) FROM notifications WHERE user_id = $1 AND read_at IS NULL", user["id"]
    )
    return {"items": [_ser(r) for r in rows], "unread": unread}


@router.post("/read-all", status_code=204)
async def mark_all_read(user: dict = Depends(current_user)):
    pool = await get_pool()
    await pool.execute(
        "UPDATE notifications SET read_at = now() WHERE user_id = $1 AND read_at IS NULL", user["id"]
    )


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(notification_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    await pool.execute(
        "UPDATE notifications SET read_at = now() WHERE id = $1 AND user_id = $2",
        notification_id, user["id"],
    )
