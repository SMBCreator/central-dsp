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
    rows = await pool.fetch(
        """SELECT id, endpoint_key, auth_token_hash, qps_limit, bid_floor, status,
                  allowed_ad_types
           FROM supply_partners WHERE status = 'active'"""
    )
    return {"partners": [dict(r) for r in rows]}


@router.get("/campaigns")
async def campaigns_feed(authorization: str | None = Header(default=None)):
    _check(authorization)
    pool = await get_pool()
    cands = await pool.fetch(
        """
        SELECT li.id AS li_id, li.bid_price, li.bid_type, li.max_bid, li.daily_budget AS li_budget,
               li.targeting, li.campaign_id, li.user_id AS owner_id,
               c.daily_budget AS camp_budget, c.frequency_cap_impressions, c.frequency_cap_period_hours,
               c.frequency_caps, c.bid_shading,
               c.pacing, c.advertiser_id, adv.domain AS adv_domain,
               cr.id AS creative_id, cr.type, cr.width, cr.height, cr.asset_url, cr.click_url,
               cr.html_snippet, cr.native_title, cr.native_description, cr.native_cta,
               cr.native_icon_url, cr.video_url, cr.video_duration, cr.vast_xml, cr.name,
               cr.imp_tracker_url, cr.click_tracker_url, cr.erid,
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
    return {
        "candidates": out,
        "domain_lists": [dict(r) for r in dl_rows],
    }
