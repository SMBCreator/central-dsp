"""
campaign_launch.py — Логика запуска рекламной кампании.

Flow:
  1. Создание кампании → статус "draft" (НЕ active!)
  2. Пользователь добавляет креативы + таргетинг
  3. POST /campaigns/{id}/launch → валидация → статус "active"
  4. Java-биддер забирает только active кампании с approved-креативами

Для MVP: при launch автоматически ставим audit_status="approved"
на все pending-креативы кампании.
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db
from models import Campaign, User
from auth import get_current_user, require_role
from rtb_bridge.models import Creative, TargetingRule

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])


# ──────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────

class CampaignLaunchResult(BaseModel):
    campaign_id: int
    status: str
    creatives_approved: int
    has_targeting: bool
    message: str


class CampaignStatusUpdate(BaseModel):
    status: str  # active | paused | stopped | archived


# ──────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────

VALID_TRANSITIONS = {
    "draft":    {"active", "archived"},
    "active":   {"paused", "stopped", "archived"},
    "paused":   {"active", "stopped", "archived"},
    "stopped":  {"active", "archived"},
    "archived": set(),
}


def _get_campaign_or_404(campaign_id: int, db: Session) -> Campaign:
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return camp


# ──────────────────────────────────────────────────────────
# POST /campaigns/{id}/launch
# Запускает кампанию из draft → active.
# ──────────────────────────────────────────────────────────

@router.post(
    "/{campaign_id}/launch",
    response_model=CampaignLaunchResult,
    summary="Запустить кампанию (draft → active)",
)
def launch_campaign(
    campaign_id: int,
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    """
    Валидация перед запуском:
      1. Кампания должна быть в статусе draft / stopped / paused
      2. bid_price > 0
      3. Хотя бы 1 active-креатив (pending → auto-approve для MVP)

    MVP-режим: автоматически одобряем (approved) все pending-креативы,
    чтобы не блокировать запуск на этапе тестирования.
    Для production убери авто-аппрув и сделай ручной модерационный flow.
    """
    camp = _get_campaign_or_404(campaign_id, db)

    # Только владелец или admin
    if current_user.role != "admin" and camp.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Проверяем допустимость перехода
    if camp.status == "active":
        raise HTTPException(
            status_code=400,
            detail="Campaign is already active",
        )
    if camp.status == "archived":
        raise HTTPException(
            status_code=400,
            detail="Cannot launch archived campaign",
        )

    # Проверяем bid_price
    bid_price = float(camp.bid_price or 0)
    if bid_price <= 0:
        raise HTTPException(
            status_code=422,
            detail="bid_price must be > 0. Set it before launching.",
        )

    # Загружаем креативы
    creatives = db.query(Creative).filter(
        Creative.campaign_id == campaign_id,
        Creative.status == "active",
    ).all()

    if not creatives:
        raise HTTPException(
            status_code=422,
            detail="No active creatives found. Add at least one creative before launching.",
        )

    # MVP auto-approve: ставим approved на все pending-креативы
    approved_count = 0
    for cr in creatives:
        if cr.audit_status == "pending":
            cr.audit_status = "approved"
            approved_count += 1
        elif cr.audit_status == "approved":
            approved_count += 1
        # rejected-креативы пропускаем

    if approved_count == 0:
        raise HTTPException(
            status_code=422,
            detail="All creatives are rejected. Upload new creatives.",
        )

    # Проверяем таргетинг (не блокирует, просто предупреждаем)
    targeting = db.query(TargetingRule).filter(
        TargetingRule.campaign_id == campaign_id
    ).first()

    # Если таргетинга нет — создаём дефолтный (без ограничений, бидим везде)
    if not targeting:
        targeting = TargetingRule(
            campaign_id=campaign_id,
            bid_floor=0.01,
            bid_ceiling=float(camp.bid_ceiling or 10.0),
            geo_countries=[],
            device_types=[],
            os_list=[],
            browser_list=[],
            site_whitelist=[],
            site_blacklist=[],
            hour_schedule={},
        )
        db.add(targeting)

    # Запускаем!
    camp.status = "active"
    db.commit()

    return CampaignLaunchResult(
        campaign_id=campaign_id,
        status="active",
        creatives_approved=approved_count,
        has_targeting=targeting is not None,
        message=(
            f"Campaign launched. {approved_count} creative(s) approved. "
            + ("Default targeting applied (no geo/device restrictions)." if not targeting else "Targeting is configured.")
        ),
    )


# ──────────────────────────────────────────────────────────
# PATCH /campaigns/{id}/status
# Пауза / остановка / архивация
# ──────────────────────────────────────────────────────────

@router.patch(
    "/{campaign_id}/status",
    summary="Изменить статус кампании (pause/stop/archive)",
)
def update_campaign_status(
    campaign_id: int,
    body: CampaignStatusUpdate,
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    camp = _get_campaign_or_404(campaign_id, db)

    if current_user.role != "admin" and camp.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    allowed = VALID_TRANSITIONS.get(camp.status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from '{camp.status}' to '{body.status}'. "
                   f"Allowed: {sorted(allowed) or 'none'}",
        )

    camp.status = body.status
    db.commit()

    return {"campaign_id": campaign_id, "status": camp.status}
