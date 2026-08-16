"""
PropellerAds adapter for CentralDSP.
Docs: https://partners.propellerads.com/api/doc

Supports: Push, Pop, Native, Interstitial campaigns.
Auth: API Key in header X-Api-Key.
"""
import httpx
import logging
from datetime import date
from typing import Dict, Any, Optional
from .base import CentralAdNetworkAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://partners.propellerads.com/v1.0"


class PropellerAdsAdapter(CentralAdNetworkAdapter):
    """
    PropellerAds ad network adapter.
    api_key  — API ключ из кабинета Partners.propellerads.com
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "X-Api-Key":   api_key,
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }

    # ──────────────────────────────────────────────
    # Campaign control
    # ──────────────────────────────────────────────

    def stop_campaign(self, external_id: str) -> bool:
        """
        PUT /campaigns/{id}/status  {"status": "stopped"}
        """
        try:
            resp = httpx.put(
                f"{BASE_URL}/campaigns/{external_id}/status",
                headers=self.headers,
                json={"status": "stopped"},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"[PropellerAds] Stopped campaign {external_id}")
            return True
        except Exception as e:
            logger.error(f"[PropellerAds] stop_campaign error: {e}")
            return False

    def start_campaign(self, external_id: str) -> bool:
        """
        PUT /campaigns/{id}/status  {"status": "active"}
        """
        try:
            resp = httpx.put(
                f"{BASE_URL}/campaigns/{external_id}/status",
                headers=self.headers,
                json={"status": "active"},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[PropellerAds] start_campaign error: {e}")
            return False

    def set_daily_budget(self, external_id: str, budget: float) -> bool:
        """Обновляет дневной бюджет кампании."""
        try:
            resp = httpx.patch(
                f"{BASE_URL}/campaigns/{external_id}",
                headers=self.headers,
                json={"dailyBudget": budget},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[PropellerAds] set_daily_budget error: {e}")
            return False

    def set_bid(self, external_id: str, bid: float) -> bool:
        """Обновляет ставку кампании."""
        try:
            resp = httpx.patch(
                f"{BASE_URL}/campaigns/{external_id}",
                headers=self.headers,
                json={"bid": bid},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[PropellerAds] set_bid error: {e}")
            return False

    # ──────────────────────────────────────────────
    # Statistics
    # ──────────────────────────────────────────────

    def get_today_stats(self) -> Dict[str, Any]:
        return self.get_stats(date.today(), date.today())

    def get_stats(self, date_from: date, date_to: date) -> Dict[str, Any]:
        """
        GET /statistics/campaigns
        ?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD
        """
        try:
            resp = httpx.get(
                f"{BASE_URL}/statistics/campaigns",
                headers=self.headers,
                params={
                    "dateFrom":  date_from.isoformat(),
                    "dateTo":    date_to.isoformat(),
                    "groupBy":   "date",
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("data", [])

            # Агрегируем по всем кампаниям
            total_clicks = sum(int(r.get("clicks", 0)) for r in rows)
            total_imps   = sum(int(r.get("impressions", 0)) for r in rows)
            total_spend  = sum(float(r.get("cost", 0)) for r in rows)
            total_conv   = sum(int(r.get("conversions", 0)) for r in rows)

            return {
                "clicks":      total_clicks,
                "impressions": total_imps,
                "spend":       total_spend,
                "conversions": total_conv,
                "balance":     self.get_balance(),
                "raw":         rows,
            }
        except Exception as e:
            logger.error(f"[PropellerAds] get_stats error: {e}")
            return {"clicks": 0, "impressions": 0, "spend": 0, "balance": 0}

    def get_campaign_stats(
        self, external_id: str, date_from: date, date_to: date
    ) -> Dict[str, Any]:
        """Статистика конкретной кампании с разбивкой по дням."""
        try:
            resp = httpx.get(
                f"{BASE_URL}/statistics/campaigns/{external_id}",
                headers=self.headers,
                params={
                    "dateFrom": date_from.isoformat(),
                    "dateTo":   date_to.isoformat(),
                    "groupBy":  "date",
                },
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"[PropellerAds] get_campaign_stats error: {e}")
            return []

    # ──────────────────────────────────────────────
    # Account
    # ──────────────────────────────────────────────

    def get_balance(self) -> float:
        """GET /account/balance"""
        try:
            resp = httpx.get(
                f"{BASE_URL}/account/balance",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            return float(resp.json().get("balance", 0))
        except Exception as e:
            logger.error(f"[PropellerAds] get_balance error: {e}")
            return 0.0

    def list_campaigns(self) -> list:
        """GET /campaigns — список всех кампаний аккаунта."""
        try:
            resp = httpx.get(
                f"{BASE_URL}/campaigns",
                headers=self.headers,
                params={"limit": 200, "offset": 0},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"[PropellerAds] list_campaigns error: {e}")
            return []
