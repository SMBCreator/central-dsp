"""Скоринг качества площадок: профили правил + отчёт по доменам/SSP.

Профиль скоринга — набор порогов (CTR, viewability, поведение из пикселя, CPA
по цели), привязываемый к кампании или группе. Приоритет: кампания > группа.
Отчёт считает per-домен / per-SSP скоринг по статистике за окно профиля.
Все запросы скоупятся по user_id сессии (в Neon+Better Auth нет RLS).
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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


# Whitelist сортировки: ключ из query -> поле строки отчёта. ORDER BY только через словарь.
_SORT_KEYS = {
    "score", "impressions", "clicks", "ctr", "viewability", "cpa", "bounce_rate",
    "avg_depth", "avg_time", "spend", "domain", "name",
}
_PAGE_SIZES = (15, 25, 50, 100)
_CSV_MAX_ROWS = 50_000
_REPORT_CACHE_TTL = 300.0
# Кэш полного (до пагинации) отчёта: ключ = user + все фильтры. Пагинация/сортировка —
# поверх кэша, поэтому листание страниц не дергает БД.
_report_cache: dict[str, tuple[float, dict, list[dict]]] = {}


def _cache_key(user_id, dimension, profile_id, campaign_id, group_id) -> str:
    return "|".join(str(x or "") for x in (user_id, dimension, profile_id, campaign_id, group_id))


def invalidate_report_cache(user_id=None) -> None:
    """Сброс кэша отчёта (после recompute / ручного блока)."""
    if user_id is None:
        _report_cache.clear()
        return
    prefix = f"{user_id}|"
    for k in [k for k in _report_cache if k.startswith(prefix)]:
        _report_cache.pop(k, None)


def _sort_rows(rows: list[dict], sort: str, order: str) -> list[dict]:
    desc = order == "desc"
    if sort in ("domain", "name"):
        return sorted(rows, key=lambda r: (r.get("domain") or r.get("name") or ""), reverse=desc)

    def key(r):
        v = r.get(sort)
        # None всегда в конце независимо от направления.
        return (v is None, (-(v or 0) if desc else (v or 0)))

    return sorted(rows, key=key)


def _apply_filters(rows: list[dict], q: str | None, only_blocked: bool, only_flagged: bool) -> list[dict]:
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in (r.get("domain") or r.get("name") or "").lower()]
    if only_blocked:
        rows = [r for r in rows if r.get("blocked")]
    if only_flagged:
        rows = [r for r in rows if r.get("flagged")]
    return rows


async def _campaign_ids_in_group(pool, user_id, group_id: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT id FROM campaigns WHERE user_id = $1 AND group_id = $2", user_id, group_id
    )
    return [str(r["id"]) for r in rows]


async def _full_report(pool, user, dimension, profile_id, campaign_id, group_id, *, force=False) -> tuple[dict, list[dict]]:
    """Полный отчёт (без пагинации) с кэшем 5 мин."""
    import time as _time

    key = _cache_key(user["id"], dimension, profile_id, campaign_id, group_id)
    hit = _report_cache.get(key)
    if hit and not force and _time.monotonic() - hit[0] < _REPORT_CACHE_TTL:
        return hit[1], hit[2]

    if campaign_id:
        owned = await pool.fetchval(
            "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
        )
        if not owned:
            raise HTTPException(404, "Кампания не найдена")

    profile = await _resolve_profile(pool, user["id"], profile_id, campaign_id, group_id)

    if dimension == "domain":
        if group_id and not campaign_id:
            # Папка кампаний: объединяем отчёты по всем кампаниям папки.
            rows_by_dom: dict[str, dict] = {}
            for cid in await _campaign_ids_in_group(pool, user["id"], group_id):
                for r in await scoring.domain_metrics(pool, str(user["id"]), profile, campaign_id=cid):
                    acc = rows_by_dom.get(r["domain"])
                    if not acc:
                        rows_by_dom[r["domain"]] = dict(r)
                        continue
                    for k in ("impressions", "clicks", "viewable_imps", "conversions"):
                        acc[k] = (acc.get(k) or 0) + (r.get(k) or 0)
                    acc["spend"] = round((acc.get("spend") or 0) + (r.get("spend") or 0), 2)
                    acc["blocked"] = acc["blocked"] or r["blocked"]
                    acc["flagged"] = acc["flagged"] or r["flagged"]
            rows = list(rows_by_dom.values())
            for m in rows:
                imps = m["impressions"]
                m["ctr"] = round(m["clicks"] / imps * 100, 3) if imps else 0.0
                m["viewability"] = round(m["viewable_imps"] / imps * 100, 1) if imps else 0.0
        else:
            rows = await scoring.domain_metrics(pool, str(user["id"]), profile, campaign_id=campaign_id)
    else:
        rows = await _ssp_rows(pool, user, profile, campaign_id)

    _report_cache[key] = (_time.monotonic(), profile, rows)
    return profile, rows


@router.get("/report")
async def scoring_report(
    profile_id: str | None = None,
    campaign_id: str | None = None,
    group_id: str | None = None,
    dimension: str = "domain",
    page: int = Query(1, ge=1),
    page_size: int = Query(25),
    sort: str = "score",
    order: str = "asc",
    q: str | None = Query(None, max_length=200),
    only_blocked: bool = False,
    only_flagged: bool = False,
    refresh: bool = False,
    user: dict = Depends(current_user),
):
    """Отчёт скоринга по площадкам (domain) или SSP (ssp) с серверной пагинацией.

    Профиль берётся явно (profile_id) либо разрешается по кампании/папке.
    Без профиля используется дефолт-набор порогов (только отчёт, без enforce).
    Ответ: {profile, dimension, rows, total, page, page_size, sort, order, generated_at, window_days}.
    """
    if dimension not in ("domain", "ssp"):
        raise HTTPException(422, "dimension: domain или ssp")
    if page_size not in _PAGE_SIZES:
        raise HTTPException(422, f"page_size: одно из {list(_PAGE_SIZES)}")
    if sort not in _SORT_KEYS:
        raise HTTPException(422, f"sort: одно из {sorted(_SORT_KEYS)}")
    if order not in ("asc", "desc"):
        raise HTTPException(422, "order: asc или desc")
    pool = await get_pool()

    profile, all_rows = await _full_report(pool, user, dimension, profile_id, campaign_id, group_id, force=refresh)
    rows = _apply_filters(all_rows, q, only_blocked, only_flagged)
    rows = _sort_rows(rows, sort, order)
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]
    generated_at = datetime.fromtimestamp(
        _report_cache[_cache_key(user["id"], dimension, profile_id, campaign_id, group_id)][0]
        - time.monotonic() + time.time(),
        tz=timezone.utc,
    ).isoformat()
    return {
        "profile": profile,
        "dimension": dimension,
        "rows": page_rows,
        "total": total,
        "total_flagged": sum(1 for r in all_rows if r.get("flagged")),
        "total_blocked": sum(1 for r in all_rows if r.get("blocked")),
        "page": page,
        "page_size": page_size,
        "sort": sort,
        "order": order,
        "window_days": int(profile.get("window_days") or 7),
        "generated_at": generated_at,
    }


_CSV_COLUMNS = [
    ("domain", "Площадка/SSP"), ("score", "Скоринг"), ("impressions", "Показы"), ("clicks", "Клики"),
    ("ctr", "CTR, %"), ("viewability", "Viewability, %"), ("bounce_rate", "Отказы, %"),
    ("avg_depth", "Глубина"), ("avg_time", "Время, с"), ("conversions", "Конверсии"),
    ("cpa", "CPA"), ("spend", "Расход"), ("flagged", "Ниже порога"), ("blocked", "Заблокирован"),
    ("block_source", "Источник блока"), ("reasons", "Сигналы"),
]


@router.get("/report.csv")
async def scoring_report_csv(
    profile_id: str | None = None,
    campaign_id: str | None = None,
    group_id: str | None = None,
    dimension: str = "domain",
    sort: str = "score",
    order: str = "asc",
    q: str | None = Query(None, max_length=200),
    only_blocked: bool = False,
    only_flagged: bool = False,
    user: dict = Depends(current_user),
):
    """CSV-выгрузка отчёта с теми же фильтрами, без пагинации (лимит 50 000 строк).

    UTF-8 с BOM и разделитель ';' — чтобы Excel в русской локали открывал сразу.
    """
    if dimension not in ("domain", "ssp"):
        raise HTTPException(422, "dimension: domain или ssp")
    if sort not in _SORT_KEYS or order not in ("asc", "desc"):
        raise HTTPException(422, "Некорректная сортировка")
    pool = await get_pool()
    profile, all_rows = await _full_report(pool, user, dimension, profile_id, campaign_id, group_id)
    rows = _sort_rows(_apply_filters(all_rows, q, only_blocked, only_flagged), sort, order)[:_CSV_MAX_ROWS]

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        yield "\ufeff"
        w.writerow([label for _, label in _CSV_COLUMNS])
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        for r in rows:
            out = []
            for key, _ in _CSV_COLUMNS:
                v = r.get("domain") if key == "domain" and r.get("domain") else r.get("name") if key == "domain" else r.get(key)
                if key == "reasons":
                    v = ", ".join(scoring.REASON_LABELS.get(c, c) for c in (v or []))
                elif isinstance(v, bool):
                    v = "да" if v else "нет"
                elif isinstance(v, float):
                    v = f"{v:.3f}".rstrip("0").rstrip(".").replace(".", ",")
                elif v is None:
                    v = ""
                out.append(v)
            w.writerow(out)
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    fname = f"scoring-{dimension}-{stamp}.csv"
    return StreamingResponse(
        gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


class ManualBlockIn(BaseModel):
    campaign_id: str
    domain: str = Field(min_length=1, max_length=253)
    note: str | None = Field(default=None, max_length=500)


def _norm_domain(raw: str) -> str:
    d = raw.strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].split(":")[0]
    if d.startswith("www."):
        d = d[4:]
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z0-9-]{2,}", d):
        raise HTTPException(422, "Некорректный домен")
    return d


@router.post("/blocks")
async def manual_block(body: ManualBlockIn, user: dict = Depends(require_platform_write)):
    """Ручная блокировка площадки в кампании. Живёт до снятия; пересчёт её не трогает."""
    pool = await get_pool()
    owned = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", body.campaign_id, user["id"]
    )
    if not owned:
        raise HTTPException(404, "Кампания не найдена")
    domain = _norm_domain(body.domain)
    reasons = json.dumps([{"type": "manual", "by": str(user["id"]), "note": body.note}])
    await pool.execute(
        """INSERT INTO scoring_blocklist (campaign_id, domain, score, reasons, source, created_by, note, updated_at)
           VALUES ($1, $2, 0, $3, 'manual', $4, $5, now())
           ON CONFLICT (campaign_id, domain) DO UPDATE
             SET source = 'manual', created_by = EXCLUDED.created_by, note = EXCLUDED.note,
                 reasons = EXCLUDED.reasons, updated_at = now()""",
        body.campaign_id, domain, reasons, user["id"], body.note,
    )
    scoring.reset_cache_for_tests()
    invalidate_report_cache(user["id"])
    return {"ok": True, "campaign_id": body.campaign_id, "domain": domain, "source": "manual"}


@router.delete("/blocks/{campaign_id}/{domain}")
async def manual_unblock(campaign_id: str, domain: str, user: dict = Depends(require_platform_write)):
    """Снятие блокировки (ручной или авто) площадки в кампании.

    Авто-блок вернётся при следующем пересчёте, если площадка всё ещё ниже порога.
    """
    pool = await get_pool()
    owned = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not owned:
        raise HTTPException(404, "Кампания не найдена")
    dom = _norm_domain(domain)
    res = await pool.execute(
        "DELETE FROM scoring_blocklist WHERE campaign_id = $1 AND domain = $2", campaign_id, dom
    )
    scoring.reset_cache_for_tests()
    invalidate_report_cache(user["id"])
    return {"ok": True, "removed": res.endswith("1") if isinstance(res, str) else True}


@router.get("/blocks")
async def list_blocks(campaign_id: str, user: dict = Depends(current_user)):
    """Текущий блок-лист кампании (авто + ручные)."""
    pool = await get_pool()
    owned = await pool.fetchval(
        "SELECT 1 FROM campaigns WHERE id = $1 AND user_id = $2", campaign_id, user["id"]
    )
    if not owned:
        raise HTTPException(404, "Кампания не найдена")
    rows = await pool.fetch(
        """SELECT domain, score, reasons, source, note, updated_at
           FROM scoring_blocklist WHERE campaign_id = $1 ORDER BY updated_at DESC""",
        campaign_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["score"] = float(d["score"]) if d["score"] is not None else None
        d["reasons"] = d["reasons"] if isinstance(d["reasons"], list) else json.loads(d["reasons"] or "[]")
        d["updated_at"] = d["updated_at"].isoformat() if d["updated_at"] else None
        out.append(d)
    return out


async def _ssp_rows(pool, user, profile, campaign_id) -> list[dict]:
    """SSP-разрез: агрегируем bid_events по supply_partner за окно профиля."""
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
            WHERE c.user_id = $1 AND e.created_at >= now() - make_interval(days => $2) {camp_filter}
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
    for m in out:
        # SSP не блокируются построчно — только наблюдение.
        m["blocked"] = False
        m["block_source"] = None
    return out


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
    invalidate_report_cache(user["id"])
    return {"ok": True, **result}
