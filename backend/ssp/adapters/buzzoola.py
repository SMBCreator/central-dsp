"""Buzzoola Adapter (buzzoola.com) — РУ native/video SSP.

Buzzoola специализируется на нативной и видео-рекламе,
большой охват Рунета (1.5B+ показов в месяц).
OpenRTB 2.5.

Настройка:
  1. Зарегистрируйтесь как DSP на buzzoola.com
  2. Предоставьте bid endpoint + технические требования
  3. Получите token и IP ranges для whitelist
"""
from ssp import SSPConnector, register


@register
class BuzzoolaConnector(SSPConnector):
    PLATFORM = "buzzoola"
    DISPLAY_NAME = "Buzzoola"
    SUPPORTED_FORMATS = ["native", "video", "banner"]
    OPENRTB_VERSION = "2.5"
    COUNTRY = "RU"

    def validate_request(self, request_headers: dict, client_ip: str) -> bool:
        token = request_headers.get("x-buzzoola-token", "") or \
                request_headers.get("x-openrtb-secret", "")
        if self.secret and token != self.secret:
            return False
        return True

    def build_test_request(self) -> dict:
        base = super().build_test_request()
        # Buzzoola: нативный формат
        base["imp"][0]["native"] = {
            "request": '{"ver":"1.1","layout":3,"adunit":2,"plcmtcnt":1}',
            "ver": "1.1",
        }
        del base["imp"][0]["banner"]
        base["imp"][0]["bidfloor"] = 2.0
        base["imp"][0]["bidfloorcur"] = "RUB"
        return base

    def parse_extensions(self, bid_req: dict) -> dict:
        ext = bid_req.get("ext", {})
        return {
            "buzzoola_format": ext.get("format"),
            "buzzoola_placement": ext.get("placement"),
        }
