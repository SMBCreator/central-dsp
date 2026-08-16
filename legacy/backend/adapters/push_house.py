import httpx
import logging
from datetime import date
from typing import Dict, Any
from .base import CentralAdNetworkAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.push.house"


class PushHouseAdapter(CentralAdNetworkAdapter):
    """
    Push.House ad network adapter for CentralDSP.
    Docs: https://api.push.house
    """

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}

    def stop_campaign(self, external_id: str) -> bool:
        try:
            resp = httpx.post(
                f"{BASE_URL}/v1/campaigns/{external_id}/stop",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"[PushHouse] Stopped campaign {external_id}")
            return True
        except Exception as e:
            logger.error(f"[PushHouse] stop_campaign error: {e}")
            return False

    def start_campaign(self, external_id: str) -> bool:
        try:
            resp = httpx.post(
                f"{BASE_URL}/v1/campaigns/{external_id}/start",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[PushHouse] start_campaign error: {e}")
            return False

    def get_today_stats(self) -> Dict[str, Any]:
        today = date.today().isoformat()
        return self.get_stats(date.today(), date.today())

    def get_stats(self, date_from: date, date_to: date) -> Dict[str, Any]:
        try:
            resp = httpx.get(
                f"{BASE_URL}/v1/statistics",
                headers=self.headers,
                params={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "clicks":      data.get("clicks", 0),
                "impressions": data.get("impressions", 0),
                "spend":       float(data.get("spent", 0)),
                "balance":     self.get_balance(),
            }
        except Exception as e:
            logger.error(f"[PushHouse] get_stats error: {e}")
            return {"clicks": 0, "impressions": 0, "spend": 0, "balance": 0}

    def get_balance(self) -> float:
        try:
            resp = httpx.get(
                f"{BASE_URL}/v1/account",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            return float(resp.json().get("balance", 0))
        except Exception as e:
            logger.error(f"[PushHouse] get_balance error: {e}")
            return 0.0
