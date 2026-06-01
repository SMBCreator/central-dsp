"""SSP Connector package.

Architecture:
  Each SSP is an adapter that wraps the OpenRTB 2.5 protocol.
  DSP acts as bidder: SSP sends BidRequest to our /rtb/bid endpoint.
  Adapter handles:
    - Endpoint registration (our bid URL → SSP dashboard)
    - Auth (shared secret / IP whitelist header)
    - SSP-specific field quirks (non-standard extensions)
    - Test bid request generation (for connectivity check)

Registry maps platform slug → adapter class.
"""
from typing import Type, Dict


class SSPConnector:
    """
    Base class for all SSP adapters.
    Each SSP that wants to send BidRequests to us:
      1. Registers our bid endpoint URL in their dashboard
      2. We validate their requests via shared secret or IP

    Subclasses define:
      - PLATFORM: str — slug (kadam, sape, getintent, ...)
      - DISPLAY_NAME: str — human name
      - SUPPORTED_FORMATS: list — banner|native|video|push
      - OPENRTB_VERSION: str — 2.4|2.5|2.6
      - build_test_request() -> dict — sample BidRequest for connectivity test
      - parse_extensions(bid_req) -> dict — extract SSP-specific ext fields
    """
    PLATFORM: str = "base"
    DISPLAY_NAME: str = "Base SSP"
    SUPPORTED_FORMATS: list = ["banner"]
    OPENRTB_VERSION: str = "2.5"
    COUNTRY: str = "RU"  # primary market

    def __init__(self, connection_config: dict):
        self.config = connection_config
        self.secret = connection_config.get("secret", "")
        self.ip_whitelist = connection_config.get("ip_whitelist", [])
        self.enabled = connection_config.get("enabled", True)

    def validate_request(self, request_headers: dict, client_ip: str) -> bool:
        """Validate incoming BidRequest is from this SSP."""
        # Check shared secret header
        secret_header = request_headers.get("x-openrtb-secret") or \
                        request_headers.get("x-ssp-token") or \
                        request_headers.get("authorization", "").replace("Bearer ", "")
        if self.secret and secret_header != self.secret:
            return False
        # Check IP whitelist (optional)
        if self.ip_whitelist and client_ip not in self.ip_whitelist:
            return False
        return True

    def get_ssp_name(self) -> str:
        return self.PLATFORM

    def build_test_request(self) -> dict:
        """Build a minimal test BidRequest to verify our endpoint works."""
        return {
            "id": "test-1234",
            "imp": [{
                "id": "1",
                "banner": {"w": 300, "h": 250},
                "bidfloor": 0.01,
                "bidfloorcur": "RUB",
            }],
            "site": {"domain": "example.ru", "page": "https://example.ru/"},
            "device": {
                "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
                "ip": "95.108.0.1",
                "geo": {"country": "RUS", "city": "Moscow"},
                "devicetype": 2,
                "os": "Windows",
                "language": "ru",
            },
            "user": {"id": "test-user-001"},
            "at": 2,
            "tmax": 150,
            "cur": ["RUB", "USD"],
        }

    def parse_extensions(self, bid_req: dict) -> dict:
        """Extract SSP-specific extension fields. Override in subclasses."""
        return {}

    def get_info(self) -> dict:
        return {
            "platform": self.PLATFORM,
            "display_name": self.DISPLAY_NAME,
            "supported_formats": self.SUPPORTED_FORMATS,
            "openrtb_version": self.OPENRTB_VERSION,
            "country": self.COUNTRY,
        }


# ── Adapter registry ─────────────────────────────────────────────────────
_REGISTRY: Dict[str, Type[SSPConnector]] = {}


def register(cls: Type[SSPConnector]):
    """Decorator to register an SSP adapter."""
    _REGISTRY[cls.PLATFORM] = cls
    return cls


def get_adapter(platform: str) -> Type[SSPConnector]:
    if platform not in _REGISTRY:
        raise ValueError(f"Unknown SSP platform: {platform}")
    return _REGISTRY[platform]


def list_adapters() -> list:
    return [
        {
            "platform": cls.PLATFORM,
            "display_name": cls.DISPLAY_NAME,
            "supported_formats": cls.SUPPORTED_FORMATS,
            "openrtb_version": cls.OPENRTB_VERSION,
            "country": cls.COUNTRY,
        }
        for cls in _REGISTRY.values()
    ]


# Import all adapters to trigger @register decorators
from ssp.adapters import kadam, sape, getintent, buzzoola, hybrid, yandex  # noqa
