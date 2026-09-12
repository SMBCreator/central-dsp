"""Client access: white-label share links (public stats) + guest viewer accounts.

Наценка (audit F-02, B14b). Доступ — это передача отчёта дальше по товарной
цепочке: получатель видит цену создателя, а создатель из MARKUP_ADD_ROLES может
добавить свой процент сверх неё. Множитель фиксируется на доступе при создании
(`markup_mult_snapshot = chain_mult(создатель) × (1 + extra/100)`) и больше не
пересчитывается — правка users.markup_pct не меняет уже выданные отчёты.
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field

from auth import current_user, hash_password_async, revoke_sessions
from db import get_pool
from rbac import (
    ADMIN_ROLES,
    MARKUP_ADD_ROLES,
    require_share_manager,
    share_multiplier,
    spend_multiplier,
)
from schemas import PasswordStr
from stats_math import clamp_hours, derive, present_spend

router = APIRouter(prefix="/share", tags=["share"])

MARKUP_MODE_INHERITED = "inherited"
MARKUP_MODE_CUSTOM = "custom_for_share"


class MarkupSnapshot(BaseModel):
    extra_pct: float
    mult: float
    mode: str


async def _markup_snapshot(user: dict, extra_pct: float | None) -> MarkupSnapshot:
    """Что увидит получатель. extra > 0 разрешён только MARKUP_ADD_ROLES —
    остальные (manager/advertiser/client/viewer) передают ровно свою цену."""
    extra = float(extra_pct or 0)
    if extra > 0 and user.get("role") not in MARKUP_ADD_ROLES:
        raise HTTPException(
            status_code=422,
            detail="Добавлять наценку к отчёту могут только суперадмин, админ и агентство",
        )
    creator_mult = await spend_multiplier(str(user["id"]), user.get("role", ""))
    return MarkupSnapshot(
        extra_pct=extra,
        mult=share_multiplier(creator_mult, extra),
        mode=MARKUP_MODE_CUSTOM if extra > 0 else MARKUP_MODE_INHERITED,
    )


def _markup_fields(d: dict) -> dict:
    mult = float(d.get("markup_mult_snapshot") or 1)
    return {
        "markup_extra_pct": float(d.get("markup_extra_pct") or 0),
        "markup_mult_snapshot": mult,
        # что видит получатель относительно net, в процентах — для UI «клиент увидит +43 %»
        "effective_markup_pct": round((mult - 1.0) * 100.0, 4),
        "markup_mode": d.get("markup_mode") or MARKUP_MODE_INHERITED,
    }


async def revoke_all_access_of(owner_id: str, by_user_id: str | None) -> None:
    """Блокировка владельца: его ссылки и гости перестают открывать отчёт сразу,
    как и его сессии (users_router.patch_user status=blocked)."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE share_links SET revoked = true, revoked_at = now(), revoked_by_user_id = $2
        WHERE user_id = $1 AND revoked_at IS NULL
        """,
        owner_id, by_user_id,
    )
    guests = await pool.fetch(
        """
        UPDATE guest_access SET revoked_at = now(), revoked_by_user_id = $2
        WHERE owner_id = $1 AND revoked_at IS NULL RETURNING user_id
        """,
        owner_id, by_user_id,
    )
    for g in guests:
        await revoke_sessions(str(g["user_id"]))


# ---------- Share links (token-based, no login) ----------

class ShareLinkIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    advertiser_id: str | None = None
    brand_name: str | None = None
    brand_color: str | None = None
    brand_logo_url: str | None = None
    expires_days: int | None = Field(default=None, ge=1, le=365)
    # добавка создателя сверх своей цены, %; None/0 — получатель видит цену создателя
    markup_extra_pct: float | None = Field(default=None, ge=0, le=500)


@router.post("/links", status_code=201)
async def create_link(body: ShareLinkIn, user: dict = Depends(require_share_manager)):
    pool = await get_pool()
    if body.advertiser_id:
        owns = await pool.fetchval(
            "SELECT 1 FROM advertisers WHERE id = $1 AND user_id = $2", body.advertiser_id, user["id"]
        )
        if not owns:
            raise HTTPException(status_code=404, detail="Рекламодатель не найден")
    markup = await _markup_snapshot(user, body.markup_extra_pct)
    token = secrets.token_urlsafe(24)
    expires_at = None
    if body.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)
    row = await pool.fetchrow(
        """
        INSERT INTO share_links (user_id, created_by_user_id, advertiser_id, token, name,
                                 brand_name, brand_color, brand_logo_url, expires_at,
                                 markup_extra_pct, markup_mult_snapshot, markup_mode)
        VALUES ($1, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING *
        """,
        user["id"], body.advertiser_id, token, body.name,
        body.brand_name, body.brand_color, body.brand_logo_url, expires_at,
        markup.extra_pct, markup.mult, markup.mode,
    )
    return _serialize_link(row)


@router.get("/links")
async def list_links(
    user: dict = Depends(current_user),
    owner_id: str | None = Query(default=None, description="admin: ссылки другого владельца"),
):
    pool = await get_pool()
    target = user["id"]
    if owner_id and owner_id != str(user["id"]):
        if user.get("role") not in ADMIN_ROLES:
            raise HTTPException(status_code=403, detail="Чужие ссылки видит только админ")
        target = owner_id
    rows = await pool.fetch(
        """
        SELECT sl.*, a.name AS advertiser_name FROM share_links sl
        LEFT JOIN advertisers a ON a.id = sl.advertiser_id
        WHERE sl.user_id = $1 ORDER BY sl.created_at DESC
        """,
        target,
    )
    return [_serialize_link(r) for r in rows]


@router.delete("/links/{link_id}", status_code=204)
async def revoke_link(link_id: str, user: dict = Depends(require_share_manager)):
    """Отзывает владелец; admin/superadmin — любую (например, при инциденте у агентства)."""
    pool = await get_pool()
    owner_clause = "" if user.get("role") in ADMIN_ROLES else " AND user_id = $2"
    args = [link_id] + ([] if user.get("role") in ADMIN_ROLES else [user["id"]])
    result = await pool.execute(
        f"""
        UPDATE share_links
           SET revoked = true, revoked_at = COALESCE(revoked_at, now()),
               revoked_by_user_id = COALESCE(revoked_by_user_id, ${len(args) + 1})
         WHERE id = $1{owner_clause}
        """,
        *args, user["id"],
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="Ссылка не найдена")


def _serialize_link(row) -> dict:
    d = dict(row)
    revoked_at = d.get("revoked_at")
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
        "revoked": bool(d.get("revoked")) or revoked_at is not None,
        "revoked_at": revoked_at.isoformat() if revoked_at else None,
        "revoked_by_user_id": str(d["revoked_by_user_id"]) if d.get("revoked_by_user_id") else None,
        "created_by_user_id": str(d["created_by_user_id"]) if d.get("created_by_user_id") else None,
        "created_at": d["created_at"].isoformat(),
        **_markup_fields(d),
    }


# ---------- Public stats by token (no auth) ----------

async def _resolve_link(token: str):
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM share_links WHERE token = $1 AND NOT revoked AND revoked_at IS NULL", token
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
    hours = min(clamp_hours(hours), 720)
    # цена получателя зафиксирована при создании ссылки — не зависит от текущих markup_pct
    mult = float(link["markup_mult_snapshot"] or 1)
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
    spend = present_spend(summary["spend"], mult)
    d = derive(bids=bids, wins=wins, impressions=imps, clicks=clicks, spend=spend)
    return {
        "brand": {
            "name": link["brand_name"] or "Отчёт по рекламе",
            "color": link["brand_color"],
            "logo_url": link["brand_logo_url"],
        },
        "report_name": link["name"],
        "summary": {
            "bids": bids, "wins": wins, "impressions": imps, "clicks": clicks,
            "spend": spend,
            "win_rate": d["win_rate"], "ctr": d["ctr"], "ecpm": d["ecpm"],
        },
        "series": [
            {
                "ts": r["ts_hour"].isoformat(),
                "bids": int(r["bids"]), "wins": int(r["wins"]),
                "impressions": int(r["impressions"]), "clicks": int(r["clicks"]),
                "spend": present_spend(r["spend"], mult),
            }
            for r in series
        ],
        "campaigns": [
            {
                "id": str(r["id"]), "name": r["name"], "status": r["status"],
                "impressions": int(r["impressions"]), "clicks": int(r["clicks"]),
                "spend": present_spend(r["spend"], mult),
                "ctr": derive(impressions=r["impressions"], clicks=r["clicks"])["ctr"],
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
    markup_extra_pct: float | None = Field(default=None, ge=0, le=500)


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
    markup = await _markup_snapshot(user, body.markup_extra_pct)
    guest = await pool.fetchrow(
        """
        INSERT INTO users (email, password_hash, name, role)
        VALUES ($1, $2, $3, 'viewer') RETURNING id, email, name, role
        """,
        body.email.lower(), await hash_password_async(body.password), body.name,
    )
    await pool.execute(
        """
        INSERT INTO guest_access (user_id, owner_id, created_by_user_id, advertiser_id,
                                  brand_name, brand_color, brand_logo_url,
                                  markup_extra_pct, markup_mult_snapshot, markup_mode)
        VALUES ($1, $2, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        guest["id"], user["id"], body.advertiser_id,
        body.brand_name, body.brand_color, body.brand_logo_url,
        markup.extra_pct, markup.mult, markup.mode,
    )
    return {
        "id": str(guest["id"]), "email": guest["email"], "name": guest["name"], "role": guest["role"],
        **_markup_fields({"markup_extra_pct": markup.extra_pct, "markup_mult_snapshot": markup.mult,
                          "markup_mode": markup.mode}),
    }


@router.get("/guests")
async def list_guests(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT u.id, u.email, u.name, u.status, ga.advertiser_id, ga.brand_name, a.name AS advertiser_name,
               ga.created_at, ga.revoked_at, ga.markup_extra_pct, ga.markup_mult_snapshot, ga.markup_mode
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
            "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
            **_markup_fields(dict(r)),
        }
        for r in rows
    ]


@router.delete("/guests/{guest_id}", status_code=204)
async def delete_guest(guest_id: str, user: dict = Depends(require_share_manager)):
    """Удаляет владелец; admin/superadmin — любого гостя."""
    pool = await get_pool()
    if user.get("role") in ADMIN_ROLES:
        result = await pool.execute(
            "DELETE FROM users WHERE id = $1 AND EXISTS (SELECT 1 FROM guest_access WHERE user_id = $1)",
            guest_id,
        )
    else:
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
               ga.revoked_at, a.name AS advertiser_name
        FROM guest_access ga LEFT JOIN advertisers a ON a.id = ga.advertiser_id
        WHERE ga.user_id = $1
        """,
        user["id"],
    )
    if not row:
        raise HTTPException(status_code=403, detail="Доступ не настроен")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=403, detail="Доступ отозван")
    return {
        "is_guest": True,
        "advertiser_id": str(row["advertiser_id"]) if row["advertiser_id"] else None,
        "advertiser_name": row["advertiser_name"],
        "brand_name": row["brand_name"],
        "brand_color": row["brand_color"],
        "brand_logo_url": row["brand_logo_url"],
    }
