from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from datetime import date


class CentralAdNetworkAdapter(ABC):
    """
    Abstract base for all ad network adapters in CentralDSP.
    Each adapter must implement these methods.
    Phase 3 will provide concrete implementations.
    """

    @abstractmethod
    def stop_campaign(self, external_id: str) -> bool:
        """Stop a campaign by its external ID in the ad network"""
        ...

    @abstractmethod
    def start_campaign(self, external_id: str) -> bool:
        """Start / resume a campaign"""
        ...

    @abstractmethod
    def get_today_stats(self) -> Dict[str, Any]:
        """
        Returns today's stats from the ad network.
        Must return at least: {clicks, impressions, spend, balance}
        """
        ...

    @abstractmethod
    def get_stats(self, date_from: date, date_to: date) -> Dict[str, Any]:
        """Returns stats for a date range"""
        ...

    @abstractmethod
    def get_balance(self) -> float:
        """Returns current account balance"""
        ...

    def test_connection(self) -> bool:
        """Test that credentials work. Default: try get_balance."""
        try:
            self.get_balance()
            return True
        except Exception:
            return False
