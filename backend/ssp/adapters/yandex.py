"""Yandex RTB Adapter (Yandex Advertising Network / YAN).

Yandex RTB — крупнейшая РУ рекламная сеть.
OpenRTB 2.5 с Yandex-специфичными расширениями.
Требует верификации как DSP-партнёра.

Ссылки:
  https://yandex.ru/dev/rtb/doc/dsp-integration.html

Важно:
  Yandex требует заключения договора DSP-партнёра.
  GDPR/152-ФЗ compliance обязателен.
  Поддерживает: banner, video, native.
  Биллинг в RUB.

Настройка:
  1. Заявка через форму на yandex.ru/dev/rtb
  2. Пройти технический аудит биддера (<80ms ответ)
  3. Получить partner_id + secret_key
"""
from ssp import SSPConnector, register


@register
class YandexConnector(SSPConnector):
    PLATFORM = "yandex"
    DISPLAY_NAME = "Yandex RTB (YAN)"
    SUPPORTED_FORMATS = ["banner", "native", "video"]
    OPENRTB_VERSION = "2.5"
    COUNTRY = "RU"

    def validate_request(self, request_headers: dict, client_ip: str) -> bool:
        # Yandex подписывает запросы HMAC-SHA256
        # Для простоты пока проверяем только shared secret
        secret = request_headers.get("x-yandex-secret", "") or \
                 request_headers.get("x-openrtb-secret", "")
        if self.secret and secret != self.secret:
            return False
        return True

    def build_test_request(self) -> dict:
        base = super().build_test_request()
        # Yandex RTB специфика
        base["imp"][0]["banner"]["w"] = 728
        base["imp"][0]["banner"]["h"] = 90
        base["imp"][0]["bidfloor"] = 5.0  # Yandex floors в RUB выше
        base["imp"][0]["bidfloorcur"] = "RUB"
        base["site"]["domain"] = "rbc.ru"
        base["ext"] = {
            "yandex": {
                "partner_id": self.config.get("partner_id", ""),
            }
        }
        return base

    def parse_extensions(self, bid_req: dict) -> dict:
        ext = bid_req.get("ext", {})
        yandex_ext = ext.get("yandex", {})
        return {
            "yandex_partner_id": yandex_ext.get("partner_id"),
            "yandex_page_id": yandex_ext.get("page_id"),
            "yandex_impid": yandex_ext.get("impid"),
        }
