"""
RTB Bridge Router — FastAPI endpoints для взаимодействия
Java биддера с CentralDSP.

Все маршруты: /api/rtb/*

Endpoints:
  GET  /api/rtb/campaigns                      — список активных кампаний для биддера
  GET  /api/rtb/creatives/{id}                 — детали конкретного креатива
  GET  /api/rtb/win                            — Win Notice по NURL (query params, SSP-стандарт)
  POST /api/rtb/win-notice                     — Win Notice по JSON body (API-стандарт)
  POST /api/rtb/loss-notice                    — Loss Notice по JSON body
  GET  /api/rtb/status                         — статус биддера (alive, QPS, win rate)
  GET  /api/rtb/stats/{campaign_id}            — RTB статистика кампании
  GET  /api/rtb/campaigns/{id}/targeting       — таргетинг кампании
  PUT  /api/rtb/campaigns/{id}/targeting       — обновить таргетинг
  GET  /api/rtb/campaigns/{id}/creatives       — список креативов кампании
  POST /api/rtb/campaigns/{id}/creatives       — создать новый креатив
  PATCH /api/rtb/creatives/{id}/status         — изменить статус/audit_status
  GET  /api/rtb/campaigns/{id}/bid-adjustments — список корректировок ставок
  POST /api/rtb/campaigns/{id}/bid-adjustments — добавить корректировку
  DELETE /api/rtb/campaigns/{id}/bid-adjustments/{adj_id} — удалить корректировку
"""
from __future__ import annotations
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Campaign
from auth import get_current_user, require_role
from models import User
from rtb_bridge.models import Creative, TargetingRule, AuctionLog, BidAdjustment
from rtb_bridge.schemas import (
    RTBCampaignOut, CreativeOut, TargetingOut,
    WinNotice, LossNotice,
    BidderStatusOut, RTBStatsOut,
)
from rtb_bridge.service import RTBService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rtb", tags=["RTB Bridge"])


# ──────────────────────────────────────────────────────────────────
# Pydantic schemas для запросов/ответов роутера
# ──────────────────────────────────────────────────────────────────

class TargetingUpdate(BaseModel):
    bid_floor: Optional[float] = None
    bid_ceiling: Optional[float] = None
    geo_countries: Optional[List[str]] = None
    device_types: Optional[List[str]] = None
    os_list: Optional[List[str]] = None
    browser_list: Optional[List[str]] = None
    site_whitelist: Optional[List[str]] = None
    site_blacklist: Optional[List[str]] = None
    hour_schedule: Optional[dict] = None


class CreativeCreate(BaseModel):
    format: str                         # banner | native | video
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    click_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    icon_url: Optional[str] = None
    sponsored_by: Optional[str] = None
    vast_url: Optional[str] = None
    video_url: Optional[str] = None
    duration_sec: Optional[int] = None
    adm: Optional[str] = None


class CreativeStatusUpdate(BaseModel):
    status: Optional[str] = None        # active|paused|archived
    audit_status: Optional[str] = None  # pending|approved|rejected
    audit_feedback: Optional[str] = None


class BidAdjustmentCreate(BaseModel):
    dimension: str   # country|device|os|hour|site
    value: str
    multiplier: float


# ──────────────────────────────────────────────────────────────────
# Кампании — для биддера (без авторизации, внутренняя сеть)
# ──────────────────────────────────────────────────────────────────

@router.get(
    "/campaigns",
    response_model=List[RTBCampaignOut],
    summary="Активные кампании для Java биддера",
)
def get_rtb_campaigns(db: Session = Depends(get_db)):
    """
    Вызывается Java биддером каждые N секунд (CampaignLoaderService).
    Возвращает кампании с таргетингом и одобренными креативами.
    Только активные кампании с остатком бюджета.
    """
    return RTBService.get_active_campaigns(db)


# ──────────────────────────────────────────────────────────────────
# Win Notice — два варианта
# ──────────────────────────────────────────────────────────────────

@router.get(
    "/win",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Win Notice via NURL (query params) — SSP-стандарт",
)
def win_notice_nurl(
    auction_id: str = Query(..., description="ID аукциона из BidRequest"),
    imp_id: str = Query(..., description="ID impression из BidRequest"),
    campaign_id: int = Query(..., description="ID кампании"),
    creative_id: int = Query(..., description="ID креатива"),
    win_price: float = Query(..., description="Цена победы в CPM USD (${AUCTION_PRICE} macro)"),
    db: Session = Depends(get_db),
):
    """
    GET /api/rtb/win?auction_id=...&imp_id=...&campaign_id=...&creative_id=...&win_price=...

    Вызывается SSP когда срабатывает NURL из BidResponse.
    Java биддер помещает этот URL в поле bid.nurl; SSP заменяет
    макрос ${AUCTION_PRICE} реальной ценой клиринга и делает GET-запрос.

    Создаёт AuctionLog(won=True) + Event(impression) для attribution chain.
    """
    notice = WinNotice(
        auction_id=auction_id,
        imp_id=imp_id,
        campaign_id=campaign_id,
        creative_id=creative_id,
        win_price=win_price,
        clearing_price=win_price,  # для первой цены = win_price
    )
    RTBService.process_win_notice(notice, db)


@router.post(
    "/win-notice",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Win Notice via JSON body — для прямых API вызовов",
)
def win_notice(
    body: WinNotice,
    db: Session = Depends(get_db),
):
    """
    POST /api/rtb/win-notice  {WinNotice JSON}

    Альтернативный эндпоинт для прямых API-вызовов (тестирование,
    внутренние сервисы). Принимает тот же WinNotice payload в теле запроса.
    """
    RTBService.process_win_notice(body, db)


# ──────────────────────────────────────────────────────────────────
# Loss Notice
# ──────────────────────────────────────────────────────────────────

@router.post(
    "/loss-notice",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Loss Notice — биддер проиграл аукцион",
)
def loss_notice(body: LossNotice, db: Session = Depends(get_db)):
    """
    Создаёт AuctionLog(won=False) для расчёта win rate.
    """
    RTBService.process_loss_notice(body, db)


# ──────────────────────────────────────────────────────────────────
# Статус биддера
# ──────────────────────────────────────────────────────────────────

@router.get(
    "/status",
    response_model=BidderStatusOut,
    summary="Статус Java биддера",
)
async def bidder_status(db: Session = Depends(get_db)):
    """Проверяет доступность биддера и возвращает метрики за сегодня."""
    return await RTBService.get_bidder_status(db)


# ──────────────────────────────────────────────────────────────────
# RTB Статистика кампании
# ──────────────────────────────────────────────────────────────────

@router.get(
    "/stats/{campaign_id}",
    response_model=RTBStatsOut,
    summary="RTB статистика кампании",
)
def campaign_rtb_stats(
    campaign_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Win rate, spend, avg CPM для кампании."""
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return RTBService.get_campaign_stats(campaign_id, db)


# ──────────────────────────────────────────────────────────────────
# Таргетинг
# ──────────────────────────────────────────────────────────────────

@router.get(
    "/campaigns/{campaign_id}/targeting",
    response_model=TargetingOut,
    summary="Получить таргетинг кампании",
)
def get_targeting(
    campaign_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rule = db.query(TargetingRule).filter(
        TargetingRule.campaign_id == campaign_id
    ).first()
    if not rule:
        return TargetingOut()
    return TargetingOut(
        bid_floor=rule.bid_floor or 0.0,
        bid_ceiling=rule.bid_ceiling or 10.0,
        geo_countries=rule.geo_countries or [],
        device_types=rule.device_types or [],
        os_list=rule.os_list or [],
        browser_list=rule.browser_list or [],
        site_whitelist=rule.site_whitelist or [],
        site_blacklist=rule.site_blacklist or [],
        hour_schedule=rule.hour_schedule or {},
    )


@router.put(
    "/campaigns/{campaign_id}/targeting",
    response_model=TargetingOut,
    summary="Создать или обновить таргетинг кампании",
)
def upsert_targeting(
    campaign_id: int,
    body: TargetingUpdate,
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")

    rule = db.query(TargetingRule).filter(
        TargetingRule.campaign_id == campaign_id
    ).first()

    if not rule:
        rule = TargetingRule(campaign_id=campaign_id)
        db.add(rule)

    if body.bid_floor is not None:
        rule.bid_floor = body.bid_floor
    if body.bid_ceiling is not None:
        rule.bid_ceiling = body.bid_ceiling
    if body.geo_countries is not None:
        rule.geo_countries = body.geo_countries
    if body.device_types is not None:
        rule.device_types = body.device_types
    if body.os_list is not None:
        rule.os_list = body.os_list
    if body.browser_list is not None:
        rule.browser_list = body.browser_list
    if body.site_whitelist is not None:
        rule.site_whitelist = body.site_whitelist
    if body.site_blacklist is not None:
        rule.site_blacklist = body.site_blacklist
    if body.hour_schedule is not None:
        rule.hour_schedule = body.hour_schedule

    db.commit()
    db.refresh(rule)

    return TargetingOut(
        bid_floor=rule.bid_floor,
        bid_ceiling=rule.bid_ceiling,
        geo_countries=rule.geo_countries or [],
        device_types=rule.device_types or [],
        os_list=rule.os_list or [],
        browser_list=rule.browser_list or [],
        site_whitelist=rule.site_whitelist or [],
        site_blacklist=rule.site_blacklist or [],
        hour_schedule=rule.hour_schedule or {},
    )


# ──────────────────────────────────────────────────────────────────
# Креативы
# ──────────────────────────────────────────────────────────────────

@router.get(
    "/campaigns/{campaign_id}/creatives",
    response_model=List[CreativeOut],
    summary="Список креативов кампании",
)
def list_creatives(
    campaign_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Creative).filter(
        Creative.campaign_id == campaign_id
    ).order_by(Creative.created_at.desc()).all()


@router.post(
    "/campaigns/{campaign_id}/creatives",
    response_model=CreativeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить новый креатив",
)
def create_creative(
    campaign_id: int,
    body: CreativeCreate,
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if body.format not in ("banner", "native", "video"):
        raise HTTPException(status_code=400, detail="format must be: banner | native | video")

    creative = Creative(
        campaign_id=campaign_id,
        format=body.format,
        width=body.width,
        height=body.height,
        image_url=body.image_url,
        click_url=body.click_url,
        title=body.title,
        description=body.description,
        icon_url=body.icon_url,
        sponsored_by=body.sponsored_by,
        vast_url=body.vast_url,
        video_url=body.video_url,
        duration_sec=body.duration_sec,
        adm=body.adm,
        status="active",
        audit_status="pending",
    )
    db.add(creative)
    db.commit()
    db.refresh(creative)
    return creative


@router.get(
    "/creatives/{creative_id}",
    response_model=CreativeOut,
    summary="Детали конкретного креатива",
)
def get_creative(
    creative_id: int,
    db: Session = Depends(get_db),
):
    creative = db.query(Creative).filter(Creative.id == creative_id).first()
    if not creative:
        raise HTTPException(status_code=404, detail="Creative not found")
    return creative


@router.patch(
    "/creatives/{creative_id}/status",
    response_model=CreativeOut,
    summary="Обновить статус / результат аудита креатива",
)
def update_creative_status(
    creative_id: int,
    body: CreativeStatusUpdate,
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    creative = db.query(Creative).filter(Creative.id == creative_id).first()
    if not creative:
        raise HTTPException(status_code=404, detail="Creative not found")

    if body.status is not None:
        if body.status not in ("active", "paused", "archived"):
            raise HTTPException(status_code=400, detail="Invalid status")
        creative.status = body.status

    if body.audit_status is not None:
        if body.audit_status not in ("pending", "approved", "rejected"):
            raise HTTPException(status_code=400, detail="Invalid audit_status")
        creative.audit_status = body.audit_status

    if body.audit_feedback is not None:
        creative.audit_feedback = body.audit_feedback

    db.commit()
    db.refresh(creative)
    return creative


# ──────────────────────────────────────────────────────────────────
# Bid Adjustments
# ──────────────────────────────────────────────────────────────────

@router.get(
    "/campaigns/{campaign_id}/bid-adjustments",
    summary="Корректировки ставок по срезам",
)
def list_bid_adjustments(
    campaign_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(BidAdjustment).filter(
        BidAdjustment.campaign_id == campaign_id
    ).order_by(BidAdjustment.dimension, BidAdjustment.value).all()
    return [
        {
            "id": r.id,
            "dimension": r.dimension,
            "value": r.value,
            "multiplier": r.multiplier,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


@router.post(
    "/campaigns/{campaign_id}/bid-adjustments",
    status_code=status.HTTP_201_CREATED,
    summary="Добавить корректировку ставки",
)
def create_bid_adjustment(
    campaign_id: int,
    body: BidAdjustmentCreate,
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    if body.dimension not in ("country", "device", "os", "hour", "site"):
        raise HTTPException(
            status_code=400,
            detail="dimension must be: country | device | os | hour | site"
        )
    if not (0.01 <= body.multiplier <= 10.0):
        raise HTTPException(status_code=400, detail="multiplier must be between 0.01 and 10.0")

    existing = db.query(BidAdjustment).filter(
        BidAdjustment.campaign_id == campaign_id,
        BidAdjustment.dimension == body.dimension,
        BidAdjustment.value == body.value,
    ).first()

    if existing:
        existing.multiplier = body.multiplier
        db.commit()
        db.refresh(existing)
        return {"id": existing.id, "dimension": existing.dimension,
                "value": existing.value, "multiplier": existing.multiplier}

    adj = BidAdjustment(
        campaign_id=campaign_id,
        dimension=body.dimension,
        value=body.value,
        multiplier=body.multiplier,
    )
    db.add(adj)
    db.commit()
    db.refresh(adj)
    return {"id": adj.id, "dimension": adj.dimension,
            "value": adj.value, "multiplier": adj.multiplier}


@router.delete(
    "/campaigns/{campaign_id}/bid-adjustments/{adj_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить корректировку ставки",
)
def delete_bid_adjustment(
    campaign_id: int,
    adj_id: int,
    current_user: User = Depends(require_role("admin", "manager")),
    db: Session = Depends(get_db),
):
    adj = db.query(BidAdjustment).filter(
        BidAdjustment.id == adj_id,
        BidAdjustment.campaign_id == campaign_id,
    ).first()
    if not adj:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    db.delete(adj)
    db.commit()
