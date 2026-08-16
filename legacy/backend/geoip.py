import httpx
import logging
from typing import Optional, Tuple
from user_agents import parse as ua_parse

logger = logging.getLogger(__name__)

# Try to load MaxMind local DB first; fall back to ip-api.com (free, 45 req/min)
try:
    import geoip2.database
    _MAXMIND_READER = geoip2.database.Reader("/app/GeoLite2-Country.mmdb")
    logger.info("MaxMind GeoLite2 loaded")
except Exception:
    _MAXMIND_READER = None
    logger.info("MaxMind not available, using ip-api.com fallback")


def get_country(ip: str) -> Optional[str]:
    """Return ISO 2-letter country code for given IP."""
    if not ip or ip in ("127.0.0.1", "::1"):
        return None
    # MaxMind local DB (no rate limits)
    if _MAXMIND_READER:
        try:
            resp = _MAXMIND_READER.country(ip)
            return resp.country.iso_code
        except Exception:
            pass
    # ip-api.com fallback (free, 45 req/min — OK for dev)
    try:
        r = httpx.get(f"http://ip-api.com/json/{ip}?fields=countryCode", timeout=2.0)
        data = r.json()
        return data.get("countryCode")
    except Exception as e:
        logger.warning(f"GeoIP lookup failed for {ip}: {e}")
        return None


def get_device_info(user_agent_str: str) -> Tuple[str, str, str]:
    """Returns (device_type, os, browser) from User-Agent string."""
    try:
        ua = ua_parse(user_agent_str or "")
        device_type = "mobile" if ua.is_mobile else ("tablet" if ua.is_tablet else "desktop")
        os_name = ua.os.family or "unknown"
        browser_name = ua.browser.family or "unknown"
        return device_type, os_name, browser_name
    except Exception:
        return "unknown", "unknown", "unknown"
