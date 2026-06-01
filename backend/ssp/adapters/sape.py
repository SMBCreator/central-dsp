"""Sape RTB Adapter (sape.ru) — контекстно-медийная РУ сеть.

Документация: https://www.sape.ru/rtb/
OpenRTB 2.5, banner/native.
Аутентификация: IP whitelist + X-Sape-Token.

Настройка:
  1. Личный кабинет Sape → Инструменты → RTB
  2. Добавить DSP endpoint: https://yourdomain.com/rtb/bid
  3. Указать список IP Sape в ip_whitelist
"""
from ssp import SSPConnector, register


@register
class SapeConnector(SSPConnector):
    PLATFORM = "sape"
    DISPLAY_NAME = "Sape RTB"
    SUPPORTED_FORMATS = ["banner", "native"]
    OPENRTB_VERSION = "2.5"
    COUNTRY = "RU"

    # Известные диапазоны IP Sape crawler/bidder серверов
    SAPE_IP_RANGES = [
        "95.215.44.",
        "95.215.45.",
        "194.165.16.",
    ]

    def validate_request(self, request_headers: dict, client_ip: str) -> bool:
        token = request_headers.get("x-sape-token", "")
        if self.secret and token != self.secret:
            return False
        # Optional strict IP check
        if self.ip_whitelist:
            if client_ip not in self.ip_whitelist:
                return False
        return True

    def build_test_request(self) -> dict:
        base = super().build_test_request()
        base["imp"][0]["banner"]["w"] = 240
        base["imp"][0]["banner"]["h"] = 400
        base["imp"][0]["bidfloor"] = 0.5
        base["imp"][0]["bidfloorcur"] = "RUB"
        base["site"]["domain"] = "news.ru"
        base["ext"] = {"sape": {"sid": "test-site-001"}}
        return base

    def parse_extensions(self, bid_req: dict) -> dict:
        ext = bid_req.get("ext", {})
        return {
            "sape_site_id": ext.get("sape", {}).get("sid"),
        }
