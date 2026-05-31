"""GeoIP resolver — ip-api.com (dev) or MaxMind GeoLite2 (prod)."""
import httpx
import logging
from typing import Optional
from backend.config import settings

logger = logging.getLogger(__name__)
_geoip_reader = None  # MaxMind reader, lazy-loaded


def _load_maxmind():
    global _geoip_reader
    try:
        import geoip2.database
        _geoip_reader = geoip2.database.Reader("/app/GeoLite2-Country.mmdb")
        logger.info("MaxMind GeoLite2 loaded")
    except Exception as e:
        logger.warning(f"MaxMind not available: {e} — falling back to ip-api.com")
        _geoip_reader = None


def get_country(ip: str) -> Optional[str]:
    """Return 2-letter country code or None."""
    if not ip or ip in ("127.0.0.1", "::1"):
        return None
    # Try MaxMind first if configured
    if settings.use_local_geoip:
        global _geoip_reader
        if _geoip_reader is None:
            _load_maxmind()
        if _geoip_reader:
            try:
                r = _geoip_reader.country(ip)
                return r.country.iso_code
            except Exception:
                pass
    # Fallback: ip-api.com (free, 45 req/min)
    try:
        resp = httpx.get(f"http://ip-api.com/json/{ip}?fields=countryCode", timeout=2.0)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("countryCode")
    except Exception:
        pass
    return None


def detect_device(user_agent: str) -> str:
    """Returns 'mobile' or 'desktop'."""
    if not user_agent:
        return "desktop"
    try:
        from user_agents import parse
        ua = parse(user_agent)
        return "mobile" if (ua.is_mobile or ua.is_tablet) else "desktop"
    except Exception:
        return "desktop"


def detect_os(user_agent: str) -> str:
    if not user_agent:
        return "unknown"
    try:
        from user_agents import parse
        ua = parse(user_agent)
        return ua.os.family or "unknown"
    except Exception:
        return "unknown"


def detect_browser(user_agent: str) -> str:
    if not user_agent:
        return "unknown"
    try:
        from user_agents import parse
        ua = parse(user_agent)
        return ua.browser.family or "unknown"
    except Exception:
        return "unknown"
