"""Helper: convert ORM models to RTB4FREE bidder JSON format.

RTB4FREE bidder polls GET /rtb/campaigns expecting a specific JSON structure.
This module handles the conversion from CentralDSP ORM -> RTB4FREE format.

Note: for the native OpenRTB 2.5 flow (SSP -> CentralDSP directly),
use rtb_bridge/router.py::_campaign_to_central() instead.
"""
from typing import Optional, List
from models import Campaign
from config import settings

BACKEND_URL = getattr(settings, "BACKEND_BASE_URL", "http://backend:8000")


def campaign_to_rtb4free(campaign: Campaign) -> Optional[dict]:
    """
    Convert CentralDSP Campaign ORM to RTB4FREE bidder campaign format.
    Returns None if campaign has no active creatives.
    """
    if campaign.status != "active":
        return None

    # FIX: campaign.targeting is uselist=False (single object, not list)
    targeting = campaign.targeting

    creatives_data = []
    for cr in campaign.creatives:
        if cr.status != "active":
            continue
        creatives_data.append({
            "id": str(cr.id),
            "type": cr.format,                      # banner|native|video
            "w": cr.width,
            "h": cr.height,
            "imageurl": cr.image_url,
            "title": cr.title,
            "desc": cr.description,
            "adomain": [],
            "adm": cr.adm or "",
            "clickurl": cr.click_url or "",
        })

    if not creatives_data:
        return None

    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "status": "active",
        "bidfloor": float(targeting.bid_floor) if targeting else settings.RTB_DEFAULT_BID_FLOOR,
        "bidceiling": float(targeting.bid_ceiling) if targeting else settings.RTB_MAX_BID_PRICE,
        "budget": float(campaign.daily_budget) if campaign.daily_budget else 0,
        "geo_whitelist": list(targeting.geo_countries or []) if targeting else [],
        "geo_blacklist": [],
        "device_types": list(targeting.device_types or []) if targeting else [],
        "os_whitelist": list(targeting.os_list or []) if targeting else [],
        "site_blacklist": list(targeting.site_blacklist or []) if targeting else [],
        "site_whitelist": list(targeting.site_whitelist or []) if targeting else [],
        "hour_schedule": dict(targeting.hour_schedule or {}) if targeting else {},
        "creatives": creatives_data,
        "win_url": f"{BACKEND_URL}/rtb/win-notice",
        "loss_url": f"{BACKEND_URL}/rtb/loss-notice",
    }


def campaigns_to_rtb4free_feed(campaigns: List[Campaign]) -> dict:
    """Convert a list of campaigns to RTB4FREE feed format."""
    rtb_campaigns = []
    for c in campaigns:
        rtb_c = campaign_to_rtb4free(c)
        if rtb_c:
            rtb_campaigns.append(rtb_c)
    return {"campaigns": rtb_campaigns, "count": len(rtb_campaigns)}
