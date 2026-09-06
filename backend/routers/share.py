"""Client access: white-label share links (public stats) + guest viewer accounts."""
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from auth import current_user, hash_password_async
from db import get_pool
from rbac import require_share_manager
from schemas import PasswordStr

router = APIRouter(prefix="/share", tags=["share"])


# ---------- Share links (token-based, no login) ----------

class ShareLinkIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    advertiser_id: str | None = None
    brand_name: str | None = None
    brand_color: str | None = None
    brand_logo_url: str | None = None
    expires_days: int | None = Field(default=None, ge=1, le=365)


@router.post("/links", status_code=201)
async def create_link(body: ShareLinkIn, user: dict = Depends(require_share_manager)):
    pool = await get_pool()
    if body.advertiser_id:
        owns = await pool.fetchval(
            "SELECT 1 FROM advertisers WHERE id = $1 AND user_id = $2", body.advertiser_id, user["id"]
        )
        if not owns:
            raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    token = secrets.token_urlsafe(24)
    expires_at = None
    if body.expires_days:
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)
    row = await pool.fetchrow(
        """
        INSERT INTO share_links (user_id, advertiser_id, token, name, brand_name, brand_color, brand_logo_url, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *
        """,
        user["id"], body.advertiser_id, token, body.name,
        body.brand_name, body.brand_color, body.brand_logo_url, expires_at,
    )
    return _serialize_link(row)


@router.get("/links")
async def list_links(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT sl.*, a.name AS advertiser_name FROM share_links sl
        LEFT JOIN advertisers a ON a.id = sl.advertiser_id
        WHERE sl.user_id = $1 ORDER BY sl.created_at DESC
        """,
        user["id"],
    )
    return [_serialize_link(r) for r in rows]


@router.delete("/links/{link_id}", status_code=204)
async def revoke_link(link_id: str, user: dict = Depends(require_share_manager)):
    pool = await get_pool()
    result = await pool.execute(
        "UPDATE share_links SET revoked = true WHERE id = $1 AND user_id = $2", link_id, user["id"]
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Ссылка не найдена")


def _serialize_link(row) -> dict:
    d = dict(row)
    return {
        "id": str(d["id"]),
        "advertiser_id": str(d["advertiser_id"]) if d.get("advertiser_id") else None,
        "advertiser_name": d.get("advertiser_name"),
        "token": d["token"],
        "name": d["name"],
        "brand_name": d.get("brand_name"),
        "brand_color": d.get("brand_color"),
        "brand_logo_url": d.get("brand_logo_url"),
        "expires_at": d["expires_at"].isoformat() if d.get("expires_at") else None,
        "revoked": d["revoked"],
        "created_at": d["created_at"].isoformat(),
    }


# ---------- Public stats by token (no auth) ----------

async def _resolve_link(token: str):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM share_links WHERE token = $1 AND NOT revoked", token
    )
    if not row:
        raise HTTPException(status_code=404, detail="Ссылка не найдена или отозвана")
    if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Срок действия ссылки истёк")
    return row


@router.get("/public/{token}")
async def public_stats(token: str, hours: int = 168):
    """Everything the public report page needs in one call."""
    link = await _resolve_link(token)
    pool = await get_pool()
    hours = min(max(hours, 1), 720)
    adv_filter = "AND c.advertiser_id = $3" if link["advertiser_id"] else ""
    args = [link["user_id"], str(hours)] + ([link["advertiser_id"]] if link["advertiser_id"] else [])

    summary = await pool.fetchrow(
        f"""
        SELECT COALESCE(SUM(s.bids),0) AS bids, COALESCE(SUM(s.wins),0) AS wins,
               COALESCE(SUM(s.impressions),0) AS impressions, COALESCE(SUM(s.clicks),0) AS clicks,
               COALESCE(SUM(s.spend),0) AS spend
        FROM stats_hourly s JOIN campaigns c ON c.id = s.campaign_id
        WHERE c.user_id = $1 AND s.ts_hour >= now() - ($2 || ' hours')::interval {adv_filter}
        """,
        *args,
    )
    series = await pool.fetch(
        f"""
        SELECT s.ts_hour, SUM(s.bids) AS bids, SUM(s.wins) AS wins,
               SUM(s.impressions) AS impressions, SUM(s.clicks) AS clicks, SUM(s.spend) AS spend
        FROM stats_hourly s JOIN campaigns c ON c.id = s.campaign_id
        WHERE c.user_id = $1 AND s.ts_hour >= now() - ($2 || ' hours')::interval {adv_filter}
        GROUP BY s.ts_hour ORDER BY s.ts_hour
        """,
        *args,
    )
    campaigns = await pool.fetch(
        f"""
        SELECT c.id, c.name, c.status,
               COALESCE(SUM(s.impressions),0) AS impressions, COALESCE(SUM(s.clicks),0) AS clicks,
               COALESCE(SUM(s.spend),0) AS spend
        FROM campaigns c
        LEFT JOIN stats_hourly s ON s.campaign_id = c.id AND s.ts_hour >= now() - ($2 || ' hours')::interval
        WHERE c.user_id = $1 {adv_filter}
        GROUP BY c.id, c.name, c.status ORDER BY spend DESC
        """,
        *args,
    )

    bids, wins = int(summary["bids"]), int(summary["wins"])
    imps, clicks = int(summary["impressions"]), int(summary["clicks"])
    spend = float(summary["spend"])
    return {
        "brand": {
            "name": link["brand_name"] or "Отчёт по рекламе",
            "color": link["brand_color"],
            "logo_url": link["brand_logo_url"],
        },
        "report_name": link["name"],
        "summary": {
            "bids": bids, "wins": wins, "impressions": imps, "clicks": clicks,
            "spend": round(spend, 4),
            "win_rate": round(wins / bids * 100, 2) if bids else 0,
            "ctr": round(clicks / imps * 100, 3) if imps else 0,
            "ecpm": round(spend / imps * 1000, 2) if imps else 0,
        },
        "series": [
            {
                "ts": r["ts_hour"].isoformat(),
                "bids": int(r["bids"]), "wins": int(r["wins"]),
                "impressions": int(r["impressions"]), "clicks": int(r["clicks"]),
                "spend": round(float(r["spend"]), 4),
            }
            for r in series
        ],
        "campaigns": [
            {
                "id": str(r["id"]), "name": r["name"], "status": r["status"],
                "impressions": int(r["impressions"]), "clicks": int(r["clicks"]),
                "spend": round(float(r["spend"]), 4),
                "ctr": round(int(r["clicks"]) / int(r["impressions"]) * 100, 3) if int(r["impressions"]) else 0,
            }
            for r in campaigns
        ],
    }


# ---------- Guest viewer accounts (login + password) ----------

class GuestIn(BaseModel):
    email: EmailStr
    password: PasswordStr
    name: str = Field(min_length=1, max_length=120)
    advertiser_id: str | None = None
    brand_name: str | None = None
    brand_color: str | None = None
    brand_logo_url: str | None = None


@router.post("/guests", status_code=201)
async def create_guest(body: GuestIn, user: dict = Depends(require_share_manager)):
    pool = await get_pool()
    if body.advertiser_id:
        owns = await pool.fetchval(
            "SELECT 1 FROM advertisers WHERE id = $1 AND user_id = $2", body.advertiser_id, user["id"]
        )
        if not owns:
            raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    exists = await pool.fetchval("SELECT 1 FROM users WHERE email = $1", body.email.lower())
    if exists:
        raise HTTPException(status_code=409, detail="Email уже занят")
    guest = await pool.fetchrow(
        """
        INSERT INTO users (email, password_hash, name, role)
        VALUES ($1, $2, $3, 'viewer') RETURNING id, email, name, role
        """,
        body.email.lower(), await hash_password_async(body.password), body.name,
    )
    await pool.execute(
        """
        INSERT INTO guest_access (user_id, owner_id, advertiser_id, brand_name, brand_color, brand_logo_url)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        guest["id"], user["id"], body.advertiser_id,
        body.brand_name, body.brand_color, body.brand_logo_url,
    )
    return {"id": str(guest["id"]), "email": guest["email"], "name": guest["name"], "role": guest["role"]}


@router.get("/guests")
async def list_guests(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT u.id, u.email, u.name, u.status, ga.advertiser_id, ga.brand_name, a.name AS advertiser_name, ga.created_at
        FROM guest_access ga
        JOIN users u ON u.id = ga.user_id
        LEFT JOIN advertisers a ON a.id = ga.advertiser_id
        WHERE ga.owner_id = $1 ORDER BY ga.created_at DESC
        """,
        user["id"],
    )
    return [
        {
            "id": str(r["id"]), "email": r["email"], "name": r["name"], "status": r["status"],
            "advertiser_id": str(r["advertiser_id"]) if r["advertiser_id"] else None,
            "advertiser_name": r["advertiser_name"],
            "brand_name": r["brand_name"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.delete("/guests/{guest_id}", status_code=204)
async def delete_guest(guest_id: str, user: dict = Depends(require_share_manager)):
    pool = await get_pool()
    result = await pool.execute(
        """
        DELETE FROM users WHERE id = $1
          AND EXISTS (SELECT 1 FROM guest_access WHERE user_id = $1 AND owner_id = $2)
        """,
        guest_id, user["id"],
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Гость не найден")


@router.get("/my-scope")
async def my_scope(user: dict = Depends(current_user)):
    """For viewer accounts: which owner/advertiser they see + branding."""
    if user["role"] != "viewer":
        return {"is_guest": False}
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT ga.owner_id, ga.advertiser_id, ga.brand_name, ga.brand_color, ga.brand_logo_url,
               a.name AS advertiser_name
        FROM guest_access ga LEFT JOIN advertisers a ON a.id = ga.advertiser_id
        WHERE ga.user_id = $1
        """,
        user["id"],
    )
    if not row:
        raise HTTPException(status_code=403, detail="Доступ не настроен")
    return {
        "is_guest": True,
        "advertiser_id": str(row["advertiser_id"]) if row["advertiser_id"] else None,
        "advertiser_name": row["advertiser_name"],
        "brand_name": row["brand_name"],
        "brand_color": row["brand_color"],
        "brand_logo_url": row["brand_logo_url"],
    }
