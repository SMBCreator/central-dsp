"""Hybrid.ai Adapter — международная programmatic платформа, активна в РФ.

Hybrid.ai работает с РУ-рынком, поддерживает все форматы.
OpenRTB 2.5/2.6, есть Header Bidding.

Настройка:
  1. partner@hybrid.ai — запрос DSP-доступа
  2. Предоставить bid endpoint + описание инвентаря
  3. Получить seat_id + shared_secret
"""
from ssp import SSPConnector, register


@register
class HybridConnector(SSPConnector):
    PLATFORM = "hybrid"
    DISPLAY_NAME = "Hybrid.ai"
    SUPPORTED_FORMATS = ["banner", "native", "video", "push"]
    OPENRTB_VERSION = "2.5"
    COUNTRY = "RU"

    def validate_request(self, request_headers: dict, client_ip: str) -> bool:
        secret = request_headers.get("x-openrtb-secret", "")
        if self.secret and secret != self.secret:
            return False
        return True

    def build_test_request(self) -> dict:
        base = super().build_test_request()
        base["imp"][0]["bidfloor"] = 0.3
        base["imp"][0]["bidfloorcur"] = "USD"
        base["ext"] = {"hybrid": {"seat": self.config.get("seat_id", "")}}
        return base

    def parse_extensions(self, bid_req: dict) -> dict:
        ext = bid_req.get("ext", {})
        return {
            "hybrid_seat": ext.get("hybrid", {}).get("seat"),
            "hybrid_ssp": ext.get("hybrid", {}).get("ssp"),
        }
