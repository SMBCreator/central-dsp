from __future__ import annotations
import logging
from sqlalchemy import select
from database import SessionLocal
from models import Campaign, Creative, TargetingRule

log = logging.getLogger(__name__)

def _fmt_creative(c):
    return {
        "id": str(c.id), "format": c.format,
        "width": c.width, "height": c.height,
        "imageUrl": c.image_url, "title": c.title,
        "description": c.description, "clickUrl": c.click_url,
        "status": c.status,
    }

def _fmt_targeting(t):
    if t is None:
        return {"bidFloor": 0.0, "bidCeiling": 10.0}
    return {
        "bidFloor": float(t.bid_floor) if t.bid_floor else 0.0,
        "bidCeiling": float(t.bid_ceiling) if t.bid_ceiling else 10.0,
        "geoCountries": t.geo_countries or [],
        "deviceTypes": t.device_types or [],
        "osList": t.os_list or [],
        "browserList": t.browser_list or [],
        "siteWhitelist": t.site_whitelist or [],
        "siteBlacklist": t.site_blacklist or [],
        "hourSchedule": t.hour_schedule or {},
    }

def get_active_campaigns_for_bidder() -> list[dict]:
    db = SessionLocal()
    try:
        camps = db.execute(
            select(Campaign).where(Campaign.status=="active")
        ).scalars().all()
        result = []
        for c in camps:
            creatives = db.execute(select(Creative).where(
                Creative.campaign_id==c.id, Creative.status=="active"
            )).scalars().all()
            targeting = db.execute(select(TargetingRule).where(
                TargetingRule.campaign_id==c.id
            )).scalar_one_or_none()
            result.append({
                "id": str(c.id), "name": c.name, "status": c.status,
                "dailyBudget": float(c.daily_budget) if c.daily_budget else None,
                "totalBudget": float(c.total_budget) if c.total_budget else None,
                "currency": "USD",
                "targeting": _fmt_targeting(targeting),
                "creatives": [_fmt_creative(cr) for cr in creatives],
            })
        return result
    except Exception as e:
        log.error("[Converter] %s", e)
        return []
    finally:
        db.close()
