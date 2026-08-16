"""
EvaDav adapter for CentralDSP.
Docs: https://evadav.com/api

Supports: Push, In-Page Push, Pop, Native campaigns.
Auth: Bearer token в заголовке Authorization.
"""
import httpx
import logging
from datetime import date
from typing import Dict, Any, List, Optional
from .base import CentralAdNetworkAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://evadav.com/api/v2"


class EvaDavAdapter(CentralAdNetworkAdapter):
    """
    EvaDav push/pop/native adapter.
    api_token — токен API из кабинета EvaDav
    """

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    # ──────────────────────────────────────────────
    # Campaign control
    # ──────────────────────────────────────────────

    def stop_campaign(self, external_id: str) -> bool:
        """
        PATCH /campaigns/{id}  {"status": "pause"}
        """
        try:
            resp = httpx.patch(
                f"{BASE_URL}/campaigns/{external_id}",
                headers=self.headers,
                json={"status": "pause"},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"[EvaDav] Paused campaign {external_id}")
            return True
        except Exception as e:
            logger.error(f"[EvaDav] stop_campaign error: {e}")
            return False

    def start_campaign(self, external_id: str) -> bool:
        try:
            resp = httpx.patch(
                f"{BASE_URL}/campaigns/{external_id}",
                headers=self.headers,
                json={"status": "active"},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[EvaDav] start_campaign error: {e}")
            return False

    def set_daily_budget(self, external_id: str, budget: float) -> bool:
        try:
            resp = httpx.patch(
                f"{BASE_URL}/campaigns/{external_id}",
                headers=self.headers,
                json={"daily_limit": budget},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[EvaDav] set_daily_budget error: {e}")
            return False

    def set_bid(self, external_id: str, bid: float) -> bool:
        """Обновляет ставку кампании."""
        try:
            resp = httpx.patch(
                f"{BASE_URL}/campaigns/{external_id}",
                headers=self.headers,
                json={"price": bid},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[EvaDav] set_bid error: {e}")
            return False

    # ──────────────────────────────────────────────
    # Statistics
    # ──────────────────────────────────────────────

    def get_today_stats(self) -> Dict[str, Any]:
        return self.get_stats(date.today(), date.today())

    def get_stats(self, date_from: date, date_to: date) -> Dict[str, Any]:
        """
        GET /statistics
        ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&group_by=date
        """
        try:
            resp = httpx.get(
                f"{BASE_URL}/statistics",
                headers=self.headers,
                params={
                    "date_from": date_from.isoformat(),
                    "date_to":   date_to.isoformat(),
                    "group_by":  "date",
                },
                timeout=20,
            )
            resp.raise_for_status()
            rows = resp.json().get("data", [])

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
            logger.error(f"[EvaDav] get_stats error: {e}")
            return {"clicks": 0, "impressions": 0, "spend": 0, "balance": 0}

    def get_stats_by_zone(
        self, external_id: str, date_from: date, date_to: date
    ) -> List[Dict[str, Any]]:
        """
        Статистика кампании с разбивкой по зонам (publishers).
        Используется для автоматического black/whitelisting.
        """
        try:
            resp = httpx.get(
                f"{BASE_URL}/statistics",
                headers=self.headers,
                params={
                    "campaign_id": external_id,
                    "date_from":   date_from.isoformat(),
                    "date_to":     date_to.isoformat(),
                    "group_by":    "zone",
                },
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"[EvaDav] get_stats_by_zone error: {e}")
            return []

    def add_to_blacklist(self, external_id: str, zone_ids: List[str]) -> bool:
        """Добавляет зоны (publisher IDs) в blacklist кампании."""
        try:
            resp = httpx.post(
                f"{BASE_URL}/campaigns/{external_id}/blacklist",
                headers=self.headers,
                json={"zone_ids": zone_ids},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"[EvaDav] Added {len(zone_ids)} zones to blacklist of campaign {external_id}")
            return True
        except Exception as e:
            logger.error(f"[EvaDav] add_to_blacklist error: {e}")
            return False

    # ──────────────────────────────────────────────
    # Account
    # ──────────────────────────────────────────────

    def get_balance(self) -> float:
        """GET /account"""
        try:
            resp = httpx.get(
                f"{BASE_URL}/account",
                headers=self.headers,
                timeout=10,
            )
            resp.raise_for_status()
            return float(resp.json().get("balance", 0))
        except Exception as e:
            logger.error(f"[EvaDav] get_balance error: {e}")
            return 0.0

    def list_campaigns(self) -> list:
        """GET /campaigns — список кампаний аккаунта."""
        try:
            resp = httpx.get(
                f"{BASE_URL}/campaigns",
                headers=self.headers,
                params={"per_page": 200, "page": 1},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"[EvaDav] list_campaigns error: {e}")
            return []
