"""GetIntent / iTargeti Adapter.

GetIntent (getintent.com) — programmatic RTB платформа, работает в РФ/СНГ.
Также известна как iTargeti после ребрендинга.
OpenRTB 2.5, все форматы.

Настройка:
  1. Обратитесь к менеджеру GetIntent для DSP-интеграции
  2. Предоставьте bid endpoint URL + ваш seat ID
  3. Они предоставят shared secret и IP ranges
"""
from ssp import SSPConnector, register


@register
class GetIntentConnector(SSPConnector):
    PLATFORM = "getintent"
    DISPLAY_NAME = "GetIntent / iTargeti"
    SUPPORTED_FORMATS = ["banner", "native", "video"]
    OPENRTB_VERSION = "2.5"
    COUNTRY = "RU"

    def validate_request(self, request_headers: dict, client_ip: str) -> bool:
        # GetIntent использует Authorization: Bearer <token>
        auth = request_headers.get("authorization", "")
        token = auth.replace("Bearer ", "").strip()
        if self.secret and token != self.secret:
            return False
        return True

    def build_test_request(self) -> dict:
        base = super().build_test_request()
        # GetIntent часто присылает video
        base["imp"][0]["video"] = {
            "mimes": ["video/mp4"],
            "minduration": 5,
            "maxduration": 30,
            "protocols": [2, 3],
            "w": 640,
            "h": 480,
        }
        base["imp"][0]["bidfloor"] = 0.5
        return base

    def parse_extensions(self, bid_req: dict) -> dict:
        return {
            "seat": bid_req.get("wseat", [None])[0],
        }
