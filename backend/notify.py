"""Helpers for in-app notifications."""
from db import get_pool


async def notify_user(user_id, type_: str, title: str, body: str = "", link: str | None = None) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO notifications (user_id, type, title, body, link) VALUES ($1,$2,$3,$4,$5)",
        user_id, type_, title, body, link,
    )


async def notify_admins(type_: str, title: str, body: str = "", link: str | None = None) -> None:
    """Notify every superadmin/admin."""
    pool = await get_pool()
    admins = await pool.fetch("SELECT id FROM users WHERE role IN ('superadmin','admin')")
    for a in admins:
        await pool.execute(
            "INSERT INTO notifications (user_id, type, title, body, link) VALUES ($1,$2,$3,$4,$5)",
            a["id"], type_, title, body, link,
        )
