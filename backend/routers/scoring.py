"""Скоринг качества площадок: профили правил + отчёт по доменам/SSP.

Профиль скоринга — набор порогов (CTR, viewability, поведение из пикселя, CPA
по цели), привязываемый к кампании или группе. Приоритет: кампания > группа.
Отчёт считает per-домен / per-SSP скоринг по статистике за окно профиля.
Все запросы скоупятся по user_id сессии (в Neon+Better Auth нет RLS).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

import scoring
from auth import current_user
from db import get_pool
from rbac import require_platform_write
from schemas import ScoringProfileIn, ScoringProfilePatch

router = APIRouter(prefix="/scoring", tags=["scoring"])

_PROFILE_COLS = (
    "id, name, description, window_days, min_impressions, min_clicks, "
    "min_ctr, min_viewability, max_bounce_rate, min_avg_depth, min_time_on_site, "
    "max_cpa, goal_id, pixel_id, block_threshold, enforce, created_at, updated_at"
)


def _profile_dict(r) -> dict:
    d = dict(r)
    for k in ("id", "goal_id", "pixel_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("min_ctr", "min_viewability", "max_bounce_rate", "min_avg_depth", "max_cpa", "block_threshold"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    for k in ("created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


@router.get("/profiles")
async def list_profiles(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        f"SELECT {_PROFILE_COLS} FROM scoring_profiles WHERE user_id = $1 ORDER BY created_at DESC",
        user["id"],
    )
    return [_profile_dict(r) for r in rows]


@router.post("/profiles")
async def create_profile(body: ScoringProfileIn, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    row = await pool.fetchrow(
        f"""INSERT INTO scoring_profiles
              (user_id, name, description, window_days, min_impressions, min_clicks,
               min_ctr, min_viewability, max_bounce_rate, min_avg_depth, min_time_on_site,
               max_cpa, goal_id, pixel_id, block_threshold, enforce)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            RETURNING {_PROFILE_COLS}""",
        user["id"], body.name, body.description, body.window_days, body.min_impressions,
        body.min_clicks, body.min_ctr, body.min_viewability, body.max_bounce_rate,
        body.min_avg_depth, body.min_time_on_site, body.max_cpa, body.goal_id, body.pixel_id,
        body.block_threshold, body.enforce,
    )
    return _profile_dict(row)


async def _owned_profile(pool, profile_id: str, user_id) -> dict:
    row = await pool.fetchrow(
        f"SELECT {_PROFILE_COLS} FROM scoring_profiles WHERE id = $1 AND user_id = $2",
        profile_id, user_id,
    )
    if not row:
        raise HTTPException(404, "Профиль скоринга не найден")
    return _profile_dict(row)


@router.patch("/profiles/{profile_id}")
async def update_profile(profile_id: str, body: ScoringProfilePatch, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    await _owned_profile(pool, profile_id, user["id"])
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return await _owned_profile(pool, profile_id, user["id"])
    sets, params = [], []
    for k, v in fields.items():
        params.append(v)
        sets.append(f"{k} = ${len(params)}")
    params.extend([profile_id, user["id"]])
    row = await pool.fetchrow(
        f"UPDATE scoring_profiles SET {', '.join(sets)}, updated_at = now() "
        f"WHERE id = ${len(params) - 1} AND user_id = ${len(params)} RETURNING {_PROFILE_COLS}",
        *params,
    )
    return _profile_dict(row)


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str, user: dict = Depends(require_platform_write)):
    pool = await get_pool()
    await _owned_profile(pool, profile_id, user["id"])
    await pool.execute("DELETE FROM scoring_profiles WHERE id = $1 AND user_id = $2", profile_id, user["id"])
    return {"ok": True}


@router.get("/report")
async def scoring_report(
    profile_id: str | None = None,
    campaign_id: str | None = None,
    group_id: str | None = None,
    dimension: str = "domain",
    user: dict = Depends(current_user),
):
    """Отчёт скоринга по площадкам (domain) или SSP (ssp).

    Профиль берётся явно (profile_id) либо разрешается по кампании/группе.
    Без профиля используется дефолт-набор порогов (только отчёт, без enforce).
    """
    if dimension not in ("domain", "ssp"):
        raise HTTPException(422, "dimension: domain или ssp")
    pool = await get_pool()

    profile = await _resolve_profile(pool, user["id"], profile_id, campaign_id, group_id)

    if dimension == "domain":
        rows = await scoring.domain_metrics(pool, str(user["id"]), profile, campaign_id=campaign_id)
        return {"profile": profile, "dimension": "domain", "rows": rows}

    # SSP-разрез: агрегируем bid_events по supply_partner за окно профиля.
    window_days = int(profile.get("window_days") or 7)
    params: list = [user["id"], window_days]
    camp_filter = ""
    if campaign_id:
        params.append(campaign_id)
        camp_filter = f" AND e.campaign_id = ${len(params)}"
    ssp_rows = await pool.fetch(
        f"""SELECT p.id, p.name,
                   count(*) FILTER (WHERE e.event_type = 'impression') AS impressions,
                   count(*) FILTER (WHERE e.event_type = 'click')      AS clicks,
                   count(*) FILTER (WHERE e.event_type = 'viewable')   AS viewable_imps,
                   COALESCE(sum(e.clear_price) FILTER (WHERE e.event_type = 'impression'),0)/1000.0 AS spend
            FROM bid_events e
            JOIN campaigns c        ON c.id = e.campaign_id
            JOIN supply_partners p  ON p.id = e.supply_partner_id
            WHERE c.user_id = $1 AND e.created_at >= now() - ($2 || ' days')::interval {camp_filter}
            GROUP BY p.id, p.name""",
        *params,
    )
    out = []
    for r in ssp_rows:
        imps, clicks, viewable = int(r["impressions"]), int(r["clicks"]), int(r["viewable_imps"])
        m = {
            "id": str(r["id"]), "name": r["name"],
            "impressions": imps, "clicks": clicks, "viewable_imps": viewable,
            "spend": round(float(r["spend"]), 2),
            "ctr": round(clicks / imps * 100, 3) if imps else 0.0,
            "viewability": round(viewable / imps * 100, 1) if imps else 0.0,
            "avg_depth": None, "avg_time": None, "bounce_rate": None, "cpa": None,
        }
        if imps >= int(profile.get("min_impressions") or 0) and clicks >= int(profile.get("min_clicks") or 0):
            score, reasons = scoring.score_domain(profile, m)
            m["score"], m["reasons"] = score, reasons
            m["flagged"] = score < float(profile.get("block_threshold") or 0.5)
        else:
            m["score"], m["reasons"], m["flagged"] = None, [], False
        out.append(m)
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else 1.0, -x["impressions"]))
    return {"profile": profile, "dimension": "ssp", "rows": out}


async def _resolve_profile(pool, user_id, profile_id, campaign_id, group_id) -> dict:
    """Явный профиль -> профиль кампании -> профиль группы -> дефолт."""
    pid = profile_id
    if not pid and campaign_id:
        pid = await pool.fetchval(
            "SELECT scoring_profile_id FROM campaigns WHERE id = $1 AND user_id = $2",
            campaign_id, user_id,
        )
        if not pid:
            gid = await pool.fetchval(
                "SELECT group_id FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user_id
            )
            if gid:
                pid = await pool.fetchval(
                    "SELECT scoring_profile_id FROM campaign_groups WHERE id = $1", gid
                )
    if not pid and group_id:
        pid = await pool.fetchval("SELECT scoring_profile_id FROM campaign_groups WHERE id = $1", group_id)
    if pid:
        row = await pool.fetchrow(
            f"SELECT {_PROFILE_COLS} FROM scoring_profiles WHERE id = $1 AND user_id = $2", pid, user_id
        )
        if row:
            return _profile_dict(row)
    # Дефолт: разумные пороги, только наблюдение.
    return {
        "id": None, "name": "Базовый (по умолчанию)", "description": None,
        "window_days": 7, "min_impressions": 200, "min_clicks": 0,
        "min_ctr": 0.05, "min_viewability": 40.0, "max_bounce_rate": None,
        "min_avg_depth": None, "min_time_on_site": None, "max_cpa": None,
        "goal_id": None, "pixel_id": None, "block_threshold": 0.5, "enforce": False,
    }


@router.post("/recompute")
async def recompute(campaign_id: str, user: dict = Depends(require_platform_write)):
    """Пересчёт scoring_blocklist кампании по её профилю (enforce=true)."""
    pool = await get_pool()
    owned = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not owned:
        raise HTTPException(404, "Кампания не найдена")
    profile = await _resolve_profile(pool, user["id"], None, campaign_id, None)
    if not profile.get("enforce"):
        raise HTTPException(400, "Профиль в режиме наблюдения (enforce выключен) — блокировка не применяется")
    result = await scoring.recompute_blocklist(pool, campaign_id, str(user["id"]), profile)
    return {"ok": True, **result}
