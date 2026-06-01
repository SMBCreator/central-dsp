"""Kadam SSP Adapter (kadam.net) — крупнейшая РУ push/banner/native SSP.

Документация: https://kadam.net/api/
OpenRTB 2.5, поддерживает banner/native/push.
Аутентификация: shared secret в заголовке X-Kadam-Token.

Настройка:
  1. Зарегистрируйтесь на kadam.net как DSP-партнёр
  2. Укажите наш bid endpoint: https://yourdomain.com/rtb/bid
  3. Получите X-Kadam-Token и занесите в SSP Connection
"""
from ssp import SSPConnector, register


@register
class KadamConnector(SSPConnector):
    PLATFORM = "kadam"
    DISPLAY_NAME = "Kadam"
    SUPPORTED_FORMATS = ["banner", "native", "push"]
    OPENRTB_VERSION = "2.5"
    COUNTRY = "RU"

    def validate_request(self, request_headers: dict, client_ip: str) -> bool:
        token = request_headers.get("x-kadam-token", "")
        if self.secret and token != self.secret:
            return False
        return True

    def build_test_request(self) -> dict:
        base = super().build_test_request()
        # Kadam sends bidfloorcur in RUB
        base["imp"][0]["bidfloorcur"] = "RUB"
        base["imp"][0]["bidfloor"] = 1.0  # 1 RUB floor
        base["ext"] = {"kadam": {"version": "2.5"}}
        return base

    def parse_extensions(self, bid_req: dict) -> dict:
        ext = bid_req.get("ext", {})
        return {
            "kadam_version": ext.get("kadam", {}).get("version"),
            "kadam_format": bid_req.get("imp", [{}])[0].get("ext", {}).get("adtype"),
        }
