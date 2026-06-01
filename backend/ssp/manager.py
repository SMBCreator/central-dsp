"""SSP Connection Manager.

Loads SSP connections from DB on startup and caches them in memory.
Used by rtb_bridge/router.py to validate incoming bid requests:
  - Check if SSP is known and active
  - Update request counters
  - IP whitelist enforcement
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, List

from sqlalchemy.orm import Session
from models import SSPConnection

logger = logging.getLogger(__name__)


class SSPManager:
    """
    In-memory registry of active SSP connections.
    Thread-safe reads (dict lookups).
    Refreshed on startup and via refresh() call.
    """

    def __init__(self):
        # platform_or_name → SSPConnection dict
        self._connections: Dict[str, dict] = {}
        self._loaded_at: Optional[datetime] = None

    def refresh(self, db: Session):
        """Reload all active SSP connections from DB."""
        conns = db.query(SSPConnection).filter(SSPConnection.is_active == True).all()  # noqa
        new_cache: Dict[str, dict] = {}
        for conn in conns:
            key = conn.platform.lower()
            new_cache[key] = {
                "id": conn.id,
                "platform": conn.platform,
                "name": conn.name,
                "secret": conn.secret,
                "ip_whitelist": list(conn.ip_whitelist or []),
                "extra_config": dict(conn.extra_config or {}),
                "is_active": conn.is_active,
            }
        self._connections = new_cache
        self._loaded_at = datetime.now(timezone.utc)
        logger.info(f"[SSPManager] Loaded {len(self._connections)} SSP connections")

    def get(self, platform: str) -> Optional[dict]:
        """Get SSP config by platform name (case-insensitive)."""
        return self._connections.get(platform.lower())

    def is_allowed_ip(self, platform: str, ip: str) -> bool:
        """
        Returns True if:
          - SSP has no IP whitelist (allow all), OR
          - IP is in whitelist
        """
        conn = self.get(platform)
        if not conn:
            return True  # unknown SSP — allowed (IP check is optional)
        whitelist = conn.get("ip_whitelist", [])
        if not whitelist:
            return True
        return ip in whitelist

    def list_platforms(self) -> List[str]:
        return list(self._connections.keys())

    @property
    def loaded_at(self) -> Optional[datetime]:
        return self._loaded_at


# Singleton instance — import this everywhere
ssp_manager = SSPManager()
