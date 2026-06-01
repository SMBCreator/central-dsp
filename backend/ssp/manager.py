"""SSP Connection Manager.

Manages all active SSP connections:
  - Loads SSPConnection records from DB
  - Instantiates the right adapter for each
  - Provides validate_request() used by /rtb/bid to identify which SSP sent the request
  - Provides get_test_request() for connectivity checks

Used by rtb_bridge/router.py to:
  1. Identify which SSP sent a BidRequest (via header/IP)
  2. Set ssp_name in AuctionLog
  3. Parse SSP-specific extensions
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session
from models import SSPConnection
from ssp import get_adapter, SSPConnector

logger = logging.getLogger(__name__)


class SSPConnectionManager:
    """
    Singleton-style manager.
    Instantiated once at startup and reused.
    Call refresh(db) to reload connections from DB.
    """

    def __init__(self):
        self._connectors: dict[str, SSPConnector] = {}  # ssp_id -> connector

    def refresh(self, db: Session):
        """Reload all active SSP connections from DB."""
        connections = db.query(SSPConnection).filter(
            SSPConnection.is_active == True
        ).all()
        self._connectors = {}
        for conn in connections:
            try:
                adapter_cls = get_adapter(conn.platform)
                self._connectors[str(conn.id)] = adapter_cls(
                    connection_config={
                        "secret": conn.secret or "",
                        "ip_whitelist": conn.ip_whitelist or [],
                        "enabled": conn.is_active,
                        **dict(conn.extra_config or {}),
                    }
                )
            except ValueError as e:
                logger.warning(f"[SSPManager] Unknown platform {conn.platform}: {e}")
        logger.info(f"[SSPManager] Loaded {len(self._connectors)} SSP connections")

    def identify_ssp(self, headers: dict, client_ip: str) -> Optional[str]:
        """
        Try to identify which SSP sent the request by validating
        headers/IP against all registered connectors.
        Returns platform slug or None.
        """
        for ssp_id, connector in self._connectors.items():
            if connector.validate_request(headers, client_ip):
                return connector.get_ssp_name()
        return None

    def get_connector(self, platform: str) -> Optional[SSPConnector]:
        for connector in self._connectors.values():
            if connector.PLATFORM == platform:
                return connector
        return None

    def get_all_connectors(self) -> list:
        return list(self._connectors.values())


# Global instance
ssp_manager = SSPConnectionManager()
