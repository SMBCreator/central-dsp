"""
Ad Networks router — управление подключёнными рекламными сетями.
CRUD для ad_accounts + тест коннекции + синхронизация баланса.
URL prefix: /api/networks
"""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime
import logging

from database import get_db
from auth import get_current_user
from models import User
from adapters import get_adapter, ADAPTER_REGISTRY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/networks", tags=["networks"])

SUPPORTED_NETWORKS = list(ADAPTER_REGISTRY.keys()) + ["rtb"]


# ─── Pydantic schemas ────────────────────────────────────────────────────────

class AdAccountCreate(BaseModel):
    network:   str  = Field(..., description=f"One of: {SUPPORTED_NETWORKS}")
    label:     str  = Field(..., min_length=1, max_length=200)
    api_token: Optional[str] = None
    api_key:   Optional[str] = None
    extra:     Optional[Dict[str, Any]] = Field(default_factory=dict)


class AdAccountUpdate(BaseModel):
    label:     Optional[str] = Field(None, min_length=1, max_length=200)
    api_token: Optional[str] = None
    api_key:   Optional[str] = None
    extra:     Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class AdAccountOut(BaseModel):
    id:             int
    user_id:        int
    network:        str
    label:          str
    is_active:      bool
    balance:        Optional[Decimal]
    last_synced_at: Optional[datetime]
    created_at:     datetime
    # Не отдаём api_token / api_key в ответе — безопасность

    class Config:
        from_attributes = True


class ConnectionTestOut(BaseModel):
    ok:      bool
    message: str
    balance: Optional[float] = None


class SyncResult(BaseModel):
    network:  str
    balance:  float
    synced_at: datetime


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_account_or_404(account_id: int, user: User, db: Session):
    from models import AdAccount
    acc = db.query(AdAccount).filter(
        AdAccount.id == account_id,
        AdAccount.user_id == user.id,
    ).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Ad account not found")
    return acc


def build_credentials(account) -> dict:
    """Собирает credentials из полей ad_account для фабрики адаптеров."""
    creds = {}
    if account.api_token:
        creds["api_token"] = account.api_token
    if account.api_key:
        creds["api_key"] = account.api_key
    if account.extra:
        creds.update(account.extra)  # client_id, token и т.д. — из extra JSONB
    return creds


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("", response_model=List[AdAccountOut])
def list_accounts(
    db:      Session = Depends(get_db),
    current: User    = Depends(get_current_user),
):
    """Список подключённых рекламных сетей пользователя."""
    from models import AdAccount
    return db.query(AdAccount).filter(
        AdAccount.user_id == current.id
    ).order_by(AdAccount.created_at.desc()).all()


@router.post("", response_model=AdAccountOut, status_code=status.HTTP_201_CREATED)
def add_account(
    payload:          AdAccountCreate,
    background_tasks: BackgroundTasks,
    db:               Session = Depends(get_db),
    current:          User    = Depends(get_current_user),
):
    """Добавить новую рекламную сеть (ad account)."""
    if payload.network not in SUPPORTED_NETWORKS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported network '{payload.network}'. Supported: {SUPPORTED_NETWORKS}",
        )

    from models import AdAccount
    acc = AdAccount(
        user_id=current.id,
        network=payload.network,
        label=payload.label,
        api_token=payload.api_token,
        api_key=payload.api_key,
        extra=payload.extra or {},
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)

    # Запускаем тест коннекции и синхронизацию баланса в фоне
    if payload.network != "rtb":
        background_tasks.add_task(_sync_balance_bg, acc.id)

    return acc


@router.get("/{account_id}", response_model=AdAccountOut)
def get_account(
    account_id: int,
    db:         Session = Depends(get_db),
    current:    User    = Depends(get_current_user),
):
    return get_account_or_404(account_id, current, db)


@router.patch("/{account_id}", response_model=AdAccountOut)
def update_account(
    account_id: int,
    payload:    AdAccountUpdate,
    db:         Session = Depends(get_db),
    current:    User    = Depends(get_current_user),
):
    acc = get_account_or_404(account_id, current, db)
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(acc, field, value)
    db.commit()
    db.refresh(acc)
    return acc


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_account(
    account_id: int,
    db:         Session = Depends(get_db),
    current:    User    = Depends(get_current_user),
):
    acc = get_account_or_404(account_id, current, db)
    db.delete(acc)
    db.commit()


@router.post("/{account_id}/test", response_model=ConnectionTestOut)
def test_connection(
    account_id: int,
    db:         Session = Depends(get_db),
    current:    User    = Depends(get_current_user),
):
    """Проверяет что API-ключ рабочий, возвращает баланс."""
    acc = get_account_or_404(account_id, current, db)

    if acc.network == "rtb":
        return ConnectionTestOut(ok=True, message="RTB bidder is managed internally", balance=None)

    try:
        adapter = get_adapter(acc.network, build_credentials(acc))
        balance = adapter.get_balance()
        return ConnectionTestOut(ok=True, message="Connection successful", balance=balance)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[test_connection] account={account_id} network={acc.network}: {e}")
        return ConnectionTestOut(ok=False, message=f"Connection failed: {str(e)[:200]}")


@router.post("/{account_id}/sync", response_model=SyncResult)
def sync_balance(
    account_id: int,
    db:         Session = Depends(get_db),
    current:    User    = Depends(get_current_user),
):
    """Принудительно синхронизировать баланс с рекламной сетью."""
    acc = get_account_or_404(account_id, current, db)

    if acc.network == "rtb":
        raise HTTPException(status_code=400, detail="RTB bidder balance is managed separately")

    try:
        adapter = get_adapter(acc.network, build_credentials(acc))
        balance = adapter.get_balance()
        acc.balance = balance
        acc.last_synced_at = datetime.utcnow()
        db.commit()
        return SyncResult(network=acc.network, balance=balance, synced_at=acc.last_synced_at)
    except Exception as e:
        logger.error(f"[sync_balance] account={account_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Sync failed: {str(e)[:200]}")


@router.get("/{account_id}/campaigns", response_model=List[Dict[str, Any]])
def list_network_campaigns(
    account_id: int,
    db:         Session = Depends(get_db),
    current:    User    = Depends(get_current_user),
):
    """Список кампаний из внешней сети (для импорта)."""
    acc = get_account_or_404(account_id, current, db)

    if acc.network == "rtb":
        raise HTTPException(status_code=400, detail="Use /api/rtb/campaigns for RTB campaigns")

    try:
        adapter = get_adapter(acc.network, build_credentials(acc))
        if hasattr(adapter, "list_campaigns"):
            return adapter.list_campaigns()
        return []
    except Exception as e:
        logger.error(f"[list_network_campaigns] account={account_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch campaigns: {str(e)[:200]}")


# ─── Background tasks ────────────────────────────────────────────────────────

def _sync_balance_bg(account_id: int):
    """Background task: синхронизировать баланс после добавления аккаунта."""
    from database import SessionLocal
    from models import AdAccount
    db = SessionLocal()
    try:
        acc = db.query(AdAccount).filter(AdAccount.id == account_id).first()
        if not acc:
            return
        adapter = get_adapter(acc.network, build_credentials(acc))
        balance = adapter.get_balance()
        acc.balance = balance
        acc.last_synced_at = datetime.utcnow()
        db.commit()
        logger.info(f"[bg_sync] account={account_id} network={acc.network} balance={balance}")
    except Exception as e:
        logger.error(f"[bg_sync] account={account_id}: {e}")
    finally:
        db.close()
