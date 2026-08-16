"""
MGID adapter for CentralDSP.
Docs: https://help.mgid.com/api

Supports: Native advertising (teaser) campaigns.
Auth: client_id + token (OAuth-like). Token передаётся в заголовке X-Access-Token.
"""
import httpx
import logging
from datetime import date
from typing import Dict, Any, Optional, List
from .base import CentralAdNetworkAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mgid.com/v1"


class MGIDAdapter(CentralAdNetworkAdapter):
    """
    MGID native advertising adapter.
    client_id — числовой ID клиента MGID
    token     — API токен из кабинета MGID
    """

    def __init__(self, client_id: str, token: str):
        self.client_id = client_id
        self.token = token
        self.headers = {
            "X-Access-Token": token,
            "Content-Type":   "application/json",
            "Accept":         "application/json",
        }

    # ──────────────────────────────────────────────
    # Campaign control
    # ──────────────────────────────────────────────

    def stop_campaign(self, external_id: str) -> bool:
        """
        POST /client/{client_id}/campaign/{id}/status
        {"status": 2}  — 1=active, 2=paused, 3=stopped
        """
        try:
            resp = httpx.post(
                f"{BASE_URL}/client/{self.client_id}/campaign/{external_id}/status",
                headers=self.headers,
                json={"status": 2},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"[MGID] Paused campaign {external_id}")
            return True
        except Exception as e:
            logger.error(f"[MGID] stop_campaign error: {e}")
            return False

    def start_campaign(self, external_id: str) -> bool:
        try:
            resp = httpx.post(
                f"{BASE_URL}/client/{self.client_id}/campaign/{external_id}/status",
                headers=self.headers,
                json={"status": 1},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[MGID] start_campaign error: {e}")
            return False

    def set_daily_budget(self, external_id: str, budget: float) -> bool:
        try:
            resp = httpx.put(
                f"{BASE_URL}/client/{self.client_id}/campaign/{external_id}",
                headers=self.headers,
                json={"dailyBudget": budget},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[MGID] set_daily_budget error: {e}")
            return False

    # ──────────────────────────────────────────────
    # Statistics
    # ──────────────────────────────────────────────

    def get_today_stats(self) -> Dict[str, Any]:
        return self.get_stats(date.today(), date.today())

    def get_stats(self, date_from: date, date_to: date) -> Dict[str, Any]:
        """
        GET /client/{client_id}/statistics/total
        ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
        """
        try:
            resp = httpx.get(
                f"{BASE_URL}/client/{self.client_id}/statistics/total",
                headers=self.headers,
                params={
                    "date_from": date_from.isoformat(),
                    "date_to":   date_to.isoformat(),
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            return {
                "clicks":      int(data.get("clicks", 0)),
                "impressions": int(data.get("impressions", 0)),
                "spend":       float(data.get("cost", 0)),
                "conversions": int(data.get("conversions", 0)),
                "ctr":         float(data.get("ctr", 0)),
                "cpc":         float(data.get("cpc", 0)),
                "balance":     self.get_balance(),
            }
        except Exception as e:
            logger.error(f"[MGID] get_stats error: {e}")
            return {"clicks": 0, "impressions": 0, "spend": 0, "balance": 0}

    def get_campaign_stats(
        self, external_id: str, date_from: date, date_to: date
    ) -> List[Dict[str, Any]]:
        """Статистика кампании с разбивкой по дням."""
        try:
            resp = httpx.get(
                f"{BASE_URL}/client/{self.client_id}/statistics/campaign/{external_id}",
                headers=self.headers,
                params={
                    "date_from": date_from.isoformat(),
                    "date_to":   date_to.isoformat(),
                    "group_by":  "date",
                },
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"[MGID] get_campaign_stats error: {e}")
            return []

    # ──────────────────────────────────────────────
    # Account
    # ──────────────────────────────────────────────

    def get_balance(self) -> float:
        """GET /client/{client_id}  → data.balance"""
        try:
            resp = httpx.get(
                f"{BASE_URL}/client/{self.client_id}",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            return float(resp.json().get("balance", 0))
        except Exception as e:
            logger.error(f"[MGID] get_balance error: {e}")
            return 0.0

    def list_campaigns(self) -> list:
        """GET /client/{client_id}/campaign — список кампаний."""
        try:
            resp = httpx.get(
                f"{BASE_URL}/client/{self.client_id}/campaign",
                headers=self.headers,
                params={"limit": 200, "offset": 0},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("campaigns", [])
        except Exception as e:
            logger.error(f"[MGID] list_campaigns error: {e}")
            return []
