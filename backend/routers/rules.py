from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from database import SessionLocal
from auth import get_current_user
from models import Rule, RuleLog, Campaign, User

router = APIRouter(prefix="/api/rules", tags=["AutoPilot Rules"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Schemas ───────────────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    campaign_id: int
    metric: str          # win_rate | cpa | roi | spend
    condition: str       # < > <= >= ==
    threshold: float
    action: str          # pause_campaign | stop_campaign | increase_bid 20 | decrease_bid 15 | notify
    is_active: bool = True


class RuleUpdate(BaseModel):
    metric: Optional[str] = None
    condition: Optional[str] = None
    threshold: Optional[float] = None
    action: Optional[str] = None
    is_active: Optional[bool] = None


class RuleOut(BaseModel):
    id: int
    campaign_id: int
    metric: str
    condition: str
    threshold: float
    action: str
    is_active: bool
    triggered: bool

    class Config:
        from_attributes = True


class RuleLogOut(BaseModel):
    id: int
    rule_id: int
    campaign_id: int
    metric_value: Optional[float]
    action_taken: Optional[str]
    created_at: Optional[str]

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────────────────────────

VALID_METRICS = {"win_rate", "cpa", "roi", "spend"}
VALID_CONDITIONS = {"<", ">", "<=", ">=", "=="}
VALID_ACTIONS_PREFIX = {"pause_campaign", "stop_campaign", "increase_bid", "decrease_bid", "notify"}


def _validate(payload):
    if hasattr(payload, "metric") and payload.metric and payload.metric not in VALID_METRICS:
        raise HTTPException(400, f"metric must be one of {VALID_METRICS}")
    if hasattr(payload, "condition") and payload.condition and payload.condition not in VALID_CONDITIONS:
        raise HTTPException(400, f"condition must be one of {VALID_CONDITIONS}")
    if hasattr(payload, "action") and payload.action:
        action_base = payload.action.strip().lower().split()[0]
        if action_base not in VALID_ACTIONS_PREFIX:
            raise HTTPException(400, f"action must start with one of {VALID_ACTIONS_PREFIX}")


def _own_campaign(db, campaign_id, user):
    c = db.execute(select(Campaign).where(
        Campaign.id == campaign_id, Campaign.user_id == user.id
    )).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: RuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _validate(payload)
    _own_campaign(db, payload.campaign_id, current_user)
    rule = Rule(**payload.model_dump(), triggered=False)
    db.add(rule); db.commit(); db.refresh(rule)
    return rule


@router.get("", response_model=List[RuleOut])
def list_rules(
    campaign_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(Rule).join(Campaign, Campaign.id == Rule.campaign_id).where(
        Campaign.user_id == current_user.id
    )
    if campaign_id:
        q = q.where(Rule.campaign_id == campaign_id)
    return db.execute(q).scalars().all()


@router.patch("/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: int,
    payload: RuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _validate(payload)
    rule = db.execute(select(Rule).where(Rule.id == rule_id)).scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Rule not found")
    _own_campaign(db, rule.campaign_id, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    db.add(rule); db.commit(); db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = db.execute(select(Rule).where(Rule.id == rule_id)).scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Rule not found")
    _own_campaign(db, rule.campaign_id, current_user)
    db.delete(rule); db.commit()


@router.get("/{rule_id}/logs", response_model=List[RuleLogOut])
def get_rule_logs(
    rule_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = db.execute(select(Rule).where(Rule.id == rule_id)).scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Rule not found")
    _own_campaign(db, rule.campaign_id, current_user)
    logs = db.execute(
        select(RuleLog).where(RuleLog.rule_id == rule_id)
        .order_by(RuleLog.id.desc()).limit(limit)
    ).scalars().all()
    return [
        RuleLogOut(
            id=l.id, rule_id=l.rule_id, campaign_id=l.campaign_id,
            metric_value=float(l.metric_value) if l.metric_value else None,
            action_taken=l.action_taken,
            created_at=str(l.created_at) if hasattr(l, "created_at") else None,
        ) for l in logs
    ]
