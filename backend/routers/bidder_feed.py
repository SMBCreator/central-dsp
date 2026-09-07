"""Internal feed for the Java bidder (Phase D).

The Java bidder keeps campaigns/partners in memory and refreshes them
periodically. These endpoints expose the same candidate rows the Python
hot path uses, so both bidders make identical decisions.

Auth: Authorization: Bearer ${RTB_API_TOKEN}. The token is shared infra
config (not a user credential) — set it in both services' environments.
"""
import hmac
import json
import os

from fastapi import APIRouter, Header, HTTPException

from db import get_pool
from ord.service import feed_gate_sql

router = APIRouter(prefix="/rtb-feed", tags=["bidder-feed"])


def _check(authorization: str | None):
    expected = os.environ.get("RTB_API_TOKEN", "")
    if not expected:
        raise HTTPException(503, "RTB_API_TOKEN is not configured")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given, expected):
        raise HTTPException(401, "bad feed token")


@router.get("/partners")
async def partners_feed(authorization: str | None = Header(default=None)):
    _check(authorization)
    pool = await get_pool()
    # active + testing: testing = онбординг SSP (реальные биды, чтобы партнёр
    # прошёл интеграционный тест). Go отдаёт 404 для всех остальных статусов —
    # см. bidder-go partnerBiddable и Python process_bid_request.
    # token_prev_hash/token_prev_valid_until — окно безразрывной ротации токена
    # (миграция 039, audit S-06): Go принимает старый токен до этого момента.
    # Протухшее окно в фид не отдаём, чтобы старый хэш не жил в памяти биддера.
    rows = await pool.fetch(
        """SELECT id, endpoint_key, auth_token_hash, qps_limit, bid_floor, status,
                  allowed_ad_types, ip_whitelist,
                  CASE WHEN token_prev_valid_until > now() THEN token_prev_hash END AS token_prev_hash,
                  CASE WHEN token_prev_valid_until > now() THEN token_prev_valid_until END AS token_prev_valid_until
           FROM supply_partners WHERE status IN ('active', 'testing')"""
    )
    return {"partners": [dict(r) for r in rows]}


@router.get("/campaigns")
async def campaigns_feed(authorization: str | None = Header(default=None)):
    _check(authorization)
    pool = await get_pool()
    # erid-гейт (38-ФЗ): креатив без erid не уходит в фид, если группа
    # показывается в РФ. Биддер этого не проверяет — и не должен.
    cands = await pool.fetch(
        f"""
        SELECT li.id AS li_id, li.bid_price, li.bid_type, li.max_bid, li.daily_budget AS li_budget,
               li.targeting, li.campaign_id, li.user_id AS owner_id,
               c.daily_budget AS camp_budget, c.frequency_cap_impressions, c.frequency_cap_period_hours,
               c.frequency_caps, c.bid_shading,
               c.pacing, c.advertiser_id, adv.domain AS adv_domain,
               adv.balance AS adv_balance, c.total_budget, c.spent_total,
               c.objective, c.clean_traffic_only,
               cr.id AS creative_id, cr.type, cr.width, cr.height, cr.asset_url, cr.click_url,
               cr.html_snippet, cr.native_title, cr.native_description, cr.native_cta,
               cr.native_icon_url, cr.video_url, cr.video_duration, cr.vast_xml, cr.name,
               cr.imp_tracker_url, cr.click_tracker_url, cr.erid,
               cr.iab_categories, cr.attrs,
               cp.name AS adv_name, cp.inn AS adv_inn, cp.ogrn AS adv_ogrn, cp.address AS adv_address
        FROM line_items li
        JOIN campaigns c ON c.id = li.campaign_id AND c.status = 'active'
             AND (c.start_date IS NULL OR c.start_date <= CURRENT_DATE)
             AND (c.end_date IS NULL OR c.end_date >= CURRENT_DATE)
             AND (c.total_budget <= 0 OR c.spent_total < c.total_budget)
        JOIN advertisers adv ON adv.id = c.advertiser_id AND adv.status = 'active' AND adv.balance > 0
        JOIN creatives cr ON cr.campaign_id = c.id AND cr.status = 'approved'
             AND (cr.line_item_id IS NULL OR cr.line_item_id = li.id)
        LEFT JOIN counterparties cp ON cp.id = cr.counterparty_id
        WHERE li.status = 'active' AND li.bid_price > 0
        {feed_gate_sql()}
        """
    )
    out = []
    for r in cands:
        d = dict(r)
        t = d.get("targeting")
        if isinstance(t, str):
            try:
                d["targeting"] = json.loads(t)
            except ValueError:
                d["targeting"] = {}
        fc = d.get("frequency_caps")
        if isinstance(fc, str):
            try:
                d["frequency_caps"] = json.loads(fc)
            except ValueError:
                d["frequency_caps"] = []
        out.append(d)

    owner_ids = list({c["owner_id"] for c in cands})
    dl_rows = []
    if owner_ids:
        dl_rows = await pool.fetch(
            "SELECT user_id, domain, list_type FROM domain_lists WHERE user_id = ANY($1)",
            owner_ids,
        )

    # Audit R-03: всё, что Python-биддер применяет из БД на каждом запросе,
    # Go получает из фида — иначе тумблеры кабинета (оптимизатор, clean traffic,
    # scoring-профиль) для Go-трафика молча не работают.
    camp_ids = list({c["campaign_id"] for c in cands})
    block_rows: list = []
    scoring_rows: list = []
    if camp_ids:
        block_rows = await pool.fetch(
            "SELECT campaign_id, block_type, block_value FROM optimizer_blocks WHERE campaign_id = ANY($1)",
            camp_ids,
        )
        scoring_rows = await pool.fetch(
            "SELECT campaign_id, domain FROM scoring_blocklist WHERE campaign_id = ANY($1)",
            camp_ids,
        )
    dirty_domains: list[str] = []
    if any(c["clean_traffic_only"] for c in cands):
        from ml.quality import CLEAN_SCORE_THRESHOLD

        dirty_domains = [
            r["domain"] for r in await pool.fetch(
                "SELECT domain FROM domain_quality WHERE score < $1", CLEAN_SCORE_THRESHOLD
            )
        ]
    # Активная pCTR-модель (ml/pctr.py): Go считает тот же лог-odds predict
    # in-process. Отдаём только если хотя бы одна кампания с performance-целью
    # на CPM — иначе множитель всё равно не применяется (guardrail Части B).
    pctr_model = None
    if any(
        c["objective"] in ("traffic", "conversions") and (c["bid_type"] or "cpm") == "cpm"
        for c in cands
    ):
        pctr_model = await _load_pctr_model(pool)

    return {
        "candidates": out,
        "domain_lists": [dict(r) for r in dl_rows],
        "pctr_model": pctr_model,
        "optimizer_blocks": [
            {"campaign_id": str(r["campaign_id"]), "block_type": r["block_type"], "block_value": r["block_value"]}
            for r in block_rows
        ],
        "scoring_blocks": [{"campaign_id": str(r["campaign_id"]), "domain": r["domain"]} for r in scoring_rows],
        "dirty_domains": dirty_domains,
    }


async def _load_pctr_model(pool) -> dict | None:
    """Активная pCTR-модель в формате для Go: тот же отбор, что ml.pctr._load_active_model.

    None, если модели нет или global_ctr вне (0,1) — Go тогда биддит базовой
    ценой (fail-safe, как Python при predict() -> None).
    """
    import json

    row = await pool.fetchrow(
        "SELECT version, metrics FROM ml_models "
        "WHERE model_type = 'pctr' AND status = 'active' ORDER BY version DESC LIMIT 1"
    )
    if not row:
        return None
    metrics = row["metrics"]
    if isinstance(metrics, str):
        metrics = json.loads(metrics or "{}")
    global_ctr = float((metrics or {}).get("global_ctr") or 0)
    if global_ctr <= 0 or global_ctr >= 1:
        return None
    segs = await pool.fetch(
        "SELECT segment_type, segment_key, ctr_smoothed FROM ml_pctr_segments WHERE model_version = $1",
        row["version"],
    )
    return {
        "version": int(row["version"]),
        "global_ctr": global_ctr,
        "segments": [
            {"type": s["segment_type"], "key": s["segment_key"], "ctr": float(s["ctr_smoothed"])}
            for s in segs
        ],
    }
