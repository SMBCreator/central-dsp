from __future__ import annotations
import datetime
from rtb_bridge.schemas import BidRequest
from models import TargetingRule

DEVICE_MAP = {1: "mobile", 2: "desktop", 3: "tv", 4: "phone", 5: "tablet"}

def matches(request: BidRequest, targeting: TargetingRule | None) -> bool:
    """Returns True if bid request passes all targeting filters."""
    if targeting is None:
        return True
    d = request.device
    country = (d.geo.country if d and d.geo else None) or ""
    os_name = (d.os or "").lower() if d else ""
    browser = (d.browser or "").lower() if d else ""
    device_int = d.devicetype if d else None

    if targeting.geo_countries and \
            country.upper() not in [c.upper() for c in targeting.geo_countries]:
        return False
    if targeting.device_types and device_int is not None:
        if DEVICE_MAP.get(device_int, "") not in [x.lower() for x in targeting.device_types]:
            return False
    if targeting.os_list and not any(o.lower() in os_name for o in targeting.os_list):
        return False
    if targeting.browser_list and not any(b.lower() in browser for b in targeting.browser_list):
        return False

    site = ""
    if request.site and request.site.domain:
        site = request.site.domain.lower()
    elif request.app and request.app.bundle:
        site = request.app.bundle.lower()

    if targeting.site_whitelist and site and \
            not any(w.lower() in site for w in targeting.site_whitelist):
        return False
    if targeting.site_blacklist and site and \
            any(b.lower() in site for b in targeting.site_blacklist):
        return False

    if targeting.hour_schedule:
        now = datetime.datetime.now(datetime.timezone.utc)
        allowed = targeting.hour_schedule.get(str(now.weekday()))
        if allowed is not None and now.hour not in allowed:
            return False
    return True
