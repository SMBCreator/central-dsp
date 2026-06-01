"""SSP Connections Router — управление подключениями к SSP.

Endpoints:
  GET    /api/ssp/platforms        — список всех доступных SSP адаптеров
  GET    /api/ssp/connections       — мои подключения (из БД)
  POST   /api/ssp/connections       — добавить новое SSP подключение
  PATCH  /api/ssp/connections/{id}  — обновить подключение
  DELETE /api/ssp/connections/{id}  — удалить подключение
  POST   /api/ssp/connections/{id}/test — тест подключения (тестовый BidRequest)
  GET    /api/ssp/bid-endpoint      — наш bid URL (для настройки в SSP dashboard)
"""
import httpx
import logging
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import SSPConnection
from auth import get_current_user, require_role
from config import settings
from ssp import list_adapters, get_adapter
from ssp.manager import ssp_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ssp", tags=["SSP Connections"])


# ── Pydantic schemas ──────────────────────────────────────────────────────

class SSPConnectionCreate(BaseModel):
    platform: str           # kadam|sape|getintent|buzzoola|hybrid|yandex
    name: str               # human label, e.g. "Kadam Main"
    secret: Optional[str] = None
    ip_whitelist: Optional[List[str]] = []
    bid_endpoint_override: Optional[str] = None  # custom bid URL if needed
    extra_config: Optional[dict] = {}            # platform-specific params
    is_active: bool = True


class SSPConnectionUpdate(BaseModel):
    name: Optional[str] = None
    secret: Optional[str] = None
    ip_whitelist: Optional[List[str]] = None
    extra_config: Optional[dict] = None
    is_active: Optional[bool] = None


class SSPConnectionOut(BaseModel):
    id: int
    platform: str
    name: str
    is_active: bool
    secret: Optional[str] = None  # masked
    ip_whitelist: Optional[List[str]] = []
    bid_endpoint: str
    extra_config: Optional[dict] = {}
    last_bid_at: Optional[datetime] = None
    total_requests: int = 0
    total_wins: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/platforms", summary="Available SSP adapters")
def list_platforms():
    """Returns all registered SSP adapters with their capabilities."""
    return {"platforms": list_adapters()}


@router.get("/bid-endpoint", summary="Our OpenRTB bid endpoint URL")
def get_bid_endpoint():
    """
    Returns our bid endpoint URL to configure in SSP dashboard.
    SSP will send BidRequests to this URL.
    """
    base = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
    return {
        "bid_url": f"{base}/rtb/bid",
        "health_url": f"{base}/rtb/health",
        "win_notice_url": f"{base}/rtb/win-notice",
        "loss_notice_url": f"{base}/rtb/loss-notice",
        "protocol": "OpenRTB 2.5",
        "method": "POST",
        "content_type": "application/json",
        "timeout_recommended_ms": 100,
        "response_no_bid": "HTTP 204 No Content",
        "response_bid": "HTTP 200 + BidResponse JSON",
    }


@router.get("/connections", response_model=List[SSPConnectionOut])
def list_connections(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    connections = db.query(SSPConnection).filter(
        SSPConnection.user_id == user.id
    ).order_by(SSPConnection.created_at.desc()).all()
    result = []
    for c in connections:
        base = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
        out = SSPConnectionOut(
            id=c.id,
            platform=c.platform,
            name=c.name,
            is_active=c.is_active,
            secret=_mask_secret(c.secret),
            ip_whitelist=c.ip_whitelist or [],
            bid_endpoint=f"{base}/rtb/bid",
            extra_config=c.extra_config or {},
            last_bid_at=c.last_bid_at,
            total_requests=c.total_requests or 0,
            total_wins=c.total_wins or 0,
            created_at=c.created_at,
        )
        result.append(out)
    return result


@router.post("/connections", response_model=SSPConnectionOut, status_code=201)
def create_connection(
    body: SSPConnectionCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    # Validate platform
    try:
        get_adapter(body.platform)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {body.platform}")

    conn = SSPConnection(
        user_id=user.id,
        platform=body.platform,
        name=body.name,
        secret=body.secret,
        ip_whitelist=body.ip_whitelist or [],
        extra_config=body.extra_config or {},
        is_active=body.is_active,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)

    # Reload manager
    ssp_manager.refresh(db)

    base = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
    return SSPConnectionOut(
        id=conn.id,
        platform=conn.platform,
        name=conn.name,
        is_active=conn.is_active,
        secret=_mask_secret(conn.secret),
        ip_whitelist=conn.ip_whitelist or [],
        bid_endpoint=f"{base}/rtb/bid",
        extra_config=conn.extra_config or {},
        created_at=conn.created_at,
    )


@router.patch("/connections/{conn_id}", response_model=SSPConnectionOut)
def update_connection(
    conn_id: int,
    body: SSPConnectionUpdate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    conn = _get_conn_or_404(conn_id, user.id, db)
    if body.name is not None:
        conn.name = body.name
    if body.secret is not None:
        conn.secret = body.secret
    if body.ip_whitelist is not None:
        conn.ip_whitelist = body.ip_whitelist
    if body.extra_config is not None:
        conn.extra_config = {**(conn.extra_config or {}), **body.extra_config}
    if body.is_active is not None:
        conn.is_active = body.is_active
    db.commit()
    db.refresh(conn)
    ssp_manager.refresh(db)
    base = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
    return SSPConnectionOut(
        id=conn.id, platform=conn.platform, name=conn.name,
        is_active=conn.is_active,
        secret=_mask_secret(conn.secret),
        ip_whitelist=conn.ip_whitelist or [],
        bid_endpoint=f"{base}/rtb/bid",
        extra_config=conn.extra_config or {},
        last_bid_at=conn.last_bid_at,
        total_requests=conn.total_requests or 0,
        total_wins=conn.total_wins or 0,
        created_at=conn.created_at,
    )


@router.delete("/connections/{conn_id}")
def delete_connection(
    conn_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    conn = _get_conn_or_404(conn_id, user.id, db)
    db.delete(conn)
    db.commit()
    ssp_manager.refresh(db)
    return {"ok": True}


@router.post("/connections/{conn_id}/test", summary="Test SSP connectivity")
async def test_connection(
    conn_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Sends a test BidRequest to our own /rtb/bid endpoint
    with the SSP's secret header, verifying:
    1. Our bidder is reachable
    2. Auth validation works
    3. We can process a BidRequest for this SSP
    Returns the BidResponse or no-bid result.
    """
    conn = _get_conn_or_404(conn_id, user.id, db)
    try:
        adapter_cls = get_adapter(conn.platform)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unknown platform")

    adapter = adapter_cls({
        "secret": conn.secret or "",
        "ip_whitelist": conn.ip_whitelist or [],
        **dict(conn.extra_config or {}),
    })
    test_req = adapter.build_test_request()

    # Auth header name per platform
    header_map = {
        "kadam": "x-kadam-token",
        "sape": "x-sape-token",
        "getintent": "authorization",
        "buzzoola": "x-buzzoola-token",
        "hybrid": "x-openrtb-secret",
        "yandex": "x-yandex-secret",
    }
    auth_header = header_map.get(conn.platform, "x-openrtb-secret")
    auth_value = conn.secret or "test"
    if conn.platform == "getintent":
        auth_value = f"Bearer {auth_value}"

    base = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
    bid_url = f"{base}/rtb/bid"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                bid_url,
                json=test_req,
                headers={
                    "Content-Type": "application/json",
                    "X-SSP-Name": conn.platform,
                    auth_header: auth_value,
                },
            )
        if resp.status_code == 204:
            return {
                "status": "ok",
                "result": "no_bid",
                "message": "Bidder reachable, no matching campaigns (no-bid 204). "
                           "Create a campaign with targeting to get bids.",
                "bid_url": bid_url,
            }
        elif resp.status_code == 200:
            return {
                "status": "ok",
                "result": "bid",
                "message": "Bidder responded with a bid!",
                "bid_response": resp.json(),
                "bid_url": bid_url,
            }
        else:
            return {
                "status": "error",
                "result": "http_error",
                "http_status": resp.status_code,
                "body": resp.text[:500],
                "bid_url": bid_url,
            }
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot reach bid endpoint: {bid_url}. "
                   f"Check BACKEND_BASE_URL in .env",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_conn_or_404(conn_id: int, user_id: int, db: Session) -> SSPConnection:
    conn = db.query(SSPConnection).filter(
        SSPConnection.id == conn_id,
        SSPConnection.user_id == user_id,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="SSP connection not found")
    return conn


def _mask_secret(secret: Optional[str]) -> Optional[str]:
    """Mask secret for UI display: show only last 4 chars."""
    if not secret:
        return None
    if len(secret) <= 4:
        return "****"
    return f"{'*' * (len(secret) - 4)}{secret[-4:]}"
