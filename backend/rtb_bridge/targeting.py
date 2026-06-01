"""Targeting filter: checks if a BidRequest matches campaign TargetingRule."""
from datetime import datetime, timezone
from typing import Optional
from models import TargetingRule
from rtb_bridge.schemas import BidRequest

# OpenRTB devicetype int -> our string
DEVICE_TYPE_MAP = {
    1: "mobile",
    2: "desktop",
    3: "ctv",
    4: "mobile",   # phone
    5: "tablet",
    6: "desktop",  # connected device
    7: "ctv",      # set-top box
}


def matches_targeting(bid_req: BidRequest, targeting: TargetingRule) -> bool:
    """
    Returns True if the BidRequest passes all targeting filters.
    Empty lists = no restriction (allow all).
    """
    # --- Geo filter ---
    if targeting.geo_countries:
        country = None
        if bid_req.device and bid_req.device.geo:
            country = bid_req.device.geo.country
        if not country or country.upper() not in [c.upper() for c in targeting.geo_countries]:
            return False

    # --- Device type filter ---
    if targeting.device_types:
        device_type_str = None
        if bid_req.device and bid_req.device.devicetype:
            device_type_str = DEVICE_TYPE_MAP.get(bid_req.device.devicetype)
        if not device_type_str or device_type_str not in targeting.device_types:
            return False

    # --- OS filter ---
    if targeting.os_list:
        os_str = None
        if bid_req.device:
            os_str = (bid_req.device.os or "").lower()
        if not os_str or not any(os_str.startswith(t.lower()) for t in targeting.os_list):
            return False

    # --- Site blacklist ---
    if targeting.site_blacklist:
        domain = None
        if bid_req.site:
            domain = bid_req.site.domain or ""
        elif bid_req.app:
            domain = bid_req.app.bundle or ""
        if domain and any(domain.endswith(bl) for bl in targeting.site_blacklist):
            return False

    # --- Site whitelist (if set, only allow listed) ---
    if targeting.site_whitelist:
        domain = None
        if bid_req.site:
            domain = bid_req.site.domain or ""
        elif bid_req.app:
            domain = bid_req.app.bundle or ""
        if not domain or not any(domain.endswith(wl) for wl in targeting.site_whitelist):
            return False

    # --- Hour schedule filter ---
    if targeting.hour_schedule:
        now = datetime.now(timezone.utc)
        weekday = str(now.weekday())  # 0=Mon ... 6=Sun
        hour = now.hour
        schedule = targeting.hour_schedule  # {"0": [9,10,11,...,18], ...}
        # If weekday is defined in schedule, check hour is allowed
        if weekday in schedule:
            if hour not in schedule[weekday]:
                return False
        # If wildcard "*" defined
        elif "*" in schedule:
            if hour not in schedule["*"]:
                return False

    return True


def get_bid_price(imp_bid_floor: float, targeting: TargetingRule) -> float:
    """
    Calculate our bid price for an impression.
    Simple strategy: bid at bid_ceiling capped by imp bid floor.
    Returns 0.0 if bid floor exceeds our ceiling (no bid).
    """
    floor = max(float(targeting.bid_floor or 0.01), imp_bid_floor)
    ceiling = float(targeting.bid_ceiling or 5.0)
    if floor > ceiling:
        return 0.0  # can't bid
    # Bid at 90% of ceiling (leave room vs competitors + second price)
    return round(ceiling * 0.90, 6)
