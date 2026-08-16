"""
RTB Bridge Service — бизнес-логика между биддером и CentralDSP.

Отвечает за:
- Формирование списка кампаний для биддера (с таргетингом и креативами)
- Обработку Win/Loss notices
- Расчёт bid_multiplier с учётом bid_adjustments
- Проверку бюджетных ограничений
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Campaign, Event
from rtb_bridge.models import (
    Creative, TargetingRule, AuctionLog, BidAdjustment
)
from rtb_bridge.schemas import (
    RTBCampaignOut, CreativeOut, TargetingOut,
    WinNotice, LossNotice, BidderStatusOut, RTBStatsOut
)

logger = logging.getLogger(__name__)


class RTBService:

    # ──────────────────────────────────────────────────────
    # Кампании для биддера
    # ──────────────────────────────────────────────────────

    @staticmethod
    def get_active_campaigns(db: Session) -> List[RTBCampaignOut]:
        """
        Возвращает активные кампании в формате, понятном RTB4FREE биддеру.
        Фильтрует кампании по:
        - status == 'active'
        - бюджет не исчерпан
        """
        campaigns = db.query(Campaign).filter(
            Campaign.status == "active"
        ).all()

        result = []
        for camp in campaigns:
            # Проверяем бюджет
            if not RTBService._has_budget(camp, db):
                continue

            # Собираем таргетинг
            targeting_rule = db.query(TargetingRule).filter(
                TargetingRule.campaign_id == camp.id
            ).first()

            targeting_out = None
            if targeting_rule:
                targeting_out = TargetingOut(
                    bid_floor=targeting_rule.bid_floor or 0.0,
                    bid_ceiling=targeting_rule.bid_ceiling or 10.0,
                    geo_countries=targeting_rule.geo_countries or [],
                    device_types=targeting_rule.device_types or [],
                    os_list=targeting_rule.os_list or [],
                    browser_list=targeting_rule.browser_list or [],
                    site_whitelist=targeting_rule.site_whitelist or [],
                    site_blacklist=targeting_rule.site_blacklist or [],
                    hour_schedule=targeting_rule.hour_schedule or {},
                )

            # Собираем активные одобренные креативы
            creatives = db.query(Creative).filter(
                Creative.campaign_id == camp.id,
                Creative.status == "active",
                Creative.audit_status == "approved",
            ).all()

            creatives_out = [
                CreativeOut(
                    id=c.id,
                    format=c.format,
                    width=c.width,
                    height=c.height,
                    image_url=c.image_url,
                    title=c.title,
                    description=c.description,
                    click_url=c.click_url,
                    adm=c.adm,
                )
                for c in creatives
            ]

            # Остаток бюджета
            budget_remaining = RTBService._get_budget_remaining(camp, db)

            result.append(RTBCampaignOut(
                id=camp.id,
                name=camp.name,
                status=camp.status,
                daily_budget=camp.daily_budget,
                total_budget=camp.total_budget,
                budget_remaining=budget_remaining,
                targeting=targeting_out,
                creatives=creatives_out,
            ))

        return result

    @staticmethod
    def _has_budget(campaign: Campaign, db: Session) -> bool:
        """Проверяет, есть ли ещё бюджет у кампании."""
        if campaign.total_budget and campaign.total_budget > 0:
            spent = RTBService._get_total_spend(campaign.id, db)
            if spent >= campaign.total_budget:
                return False

        if campaign.daily_budget and campaign.daily_budget > 0:
            daily_spent = RTBService._get_daily_spend(campaign.id, db)
            if daily_spent >= campaign.daily_budget:
                return False

        return True

    @staticmethod
    def _get_total_spend(campaign_id: int, db: Session) -> float:
        """Суммарный расход кампании из auction_logs."""
        result = db.query(func.sum(AuctionLog.win_price)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.won == True,
        ).scalar()
        return float(result or 0.0)

    @staticmethod
    def _get_daily_spend(campaign_id: int, db: Session) -> float:
        """Расход за сегодня."""
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result = db.query(func.sum(AuctionLog.win_price)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.won == True,
            AuctionLog.created_at >= today_start,
        ).scalar()
        return float(result or 0.0)

    @staticmethod
    def _get_budget_remaining(campaign: Campaign, db: Session) -> Optional[float]:
        """Возвращает минимальный из доступных остатков бюджета."""
        remaining = None

        if campaign.total_budget and campaign.total_budget > 0:
            spent = RTBService._get_total_spend(campaign.id, db)
            remaining = campaign.total_budget - spent

        if campaign.daily_budget and campaign.daily_budget > 0:
            daily_spent = RTBService._get_daily_spend(campaign.id, db)
            daily_remaining = campaign.daily_budget - daily_spent
            remaining = min(remaining, daily_remaining) if remaining is not None else daily_remaining

        return remaining

    # ──────────────────────────────────────────────────────
    # Win / Loss Notice обработка
    # ──────────────────────────────────────────────────────

    @staticmethod
    def process_win_notice(notice: WinNotice, db: Session) -> AuctionLog:
        """
        Биддер выиграл аукцион.
        1. Записываем AuctionLog(won=True)
        2. Создаём Event(event_name='impression') для attribution chain
        """
        log = AuctionLog(
            campaign_id=notice.campaign_id,
            creative_id=notice.creative_id,
            auction_id=notice.auction_id,
            imp_id=notice.imp_id,
            win_price=notice.win_price,
            clearing_price=notice.clearing_price,
            won=True,
            site_domain=notice.site_domain,
            app_bundle=notice.app_bundle,
            geo_country=notice.geo_country,
            device_type=notice.device_type,
        )
        db.add(log)

        # Attribution: impression event привязан к auction_id
        # Позволяет связать impression → click → conversion
        impression_event = Event(
            pixel_id=None,              # RTB impression, не JS pixel
            event_name="impression",
            sub_id="rtb",
            creative_id=str(notice.creative_id),
            value=notice.win_price / 1000.0,  # CPM → cost per impression
            currency="USD",
            country_code=notice.geo_country,
            device_type=notice.device_type,
            metadata_={
                "auction_id": notice.auction_id,
                "imp_id": notice.imp_id,
                "win_price_cpm": notice.win_price,
                "site_domain": notice.site_domain,
                "app_bundle": notice.app_bundle,
                "source": "rtb_win",
            },
        )
        db.add(impression_event)
        db.commit()
        db.refresh(log)

        logger.info(
            f"[WIN] campaign={notice.campaign_id} creative={notice.creative_id} "
            f"price={notice.win_price:.4f} site={notice.site_domain}"
        )
        return log

    @staticmethod
    def process_loss_notice(notice: LossNotice, db: Session) -> AuctionLog:
        """
        Биддер проиграл аукцион.
        Записываем AuctionLog(won=False) для статистики win rate.
        """
        log = AuctionLog(
            campaign_id=notice.campaign_id,
            creative_id=notice.creative_id,
            auction_id=notice.auction_id,
            imp_id=notice.imp_id,
            won=False,
            loss_reason=notice.loss_reason,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    # ──────────────────────────────────────────────────────
    # Статистика
    # ──────────────────────────────────────────────────────

    @staticmethod
    def get_campaign_stats(campaign_id: int, db: Session) -> RTBStatsOut:
        """RTB статистика кампании за все время."""
        total = db.query(
            func.count(AuctionLog.id).label("auctions"),
            func.sum(
                func.cast(AuctionLog.won, db.bind.dialect.type_descriptor(None).__class__)
                if False else AuctionLog.won.cast(None)
            ).label("wins"),
        ).filter(AuctionLog.campaign_id == campaign_id).first()

        # Используем прямые запросы для ясности
        auctions = db.query(func.count(AuctionLog.id)).filter(
            AuctionLog.campaign_id == campaign_id
        ).scalar() or 0

        wins = db.query(func.count(AuctionLog.id)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.won == True,
        ).scalar() or 0

        avg_bid = db.query(func.avg(AuctionLog.bid_price)).filter(
            AuctionLog.campaign_id == campaign_id,
        ).scalar() or 0.0

        avg_win = db.query(func.avg(AuctionLog.win_price)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.won == True,
        ).scalar() or 0.0

        total_spend = db.query(func.sum(AuctionLog.win_price)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.won == True,
        ).scalar() or 0.0

        losses = auctions - wins
        win_rate = (wins / auctions * 100) if auctions else 0.0
        avg_cpm = float(avg_win) if avg_win else 0.0

        return RTBStatsOut(
            campaign_id=campaign_id,
            auctions=auctions,
            wins=wins,
            losses=losses,
            win_rate_pct=round(win_rate, 2),
            avg_bid_price=round(float(avg_bid), 4),
            avg_win_price=round(float(avg_win), 4),
            total_spend=round(float(total_spend), 4),
            avg_cpm=round(avg_cpm, 4),
        )

    # ──────────────────────────────────────────────────────
    # Статус биддера
    # ──────────────────────────────────────────────────────

    @staticmethod
    async def get_bidder_status(db: Session) -> BidderStatusOut:
        """Проверяет доступность RTB4FREE биддера по HTTP."""
        bidder_url = getattr(settings, "BIDDER_URL", "http://bidder:8080")
        alive = False

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{bidder_url}/health")
                alive = resp.status_code == 200
        except Exception as e:
            logger.warning(f"[BIDDER STATUS] Cannot reach bidder: {e}")

        # Статистика сегодня
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        auctions_today = db.query(func.count(AuctionLog.id)).filter(
            AuctionLog.created_at >= today_start
        ).scalar() or 0

        wins_today = db.query(func.count(AuctionLog.id)).filter(
            AuctionLog.created_at >= today_start,
            AuctionLog.won == True,
        ).scalar() or 0

        spend_today = db.query(func.sum(AuctionLog.win_price)).filter(
            AuctionLog.created_at >= today_start,
            AuctionLog.won == True,
        ).scalar() or 0.0

        win_rate = (wins_today / auctions_today * 100) if auctions_today else 0.0

        return BidderStatusOut(
            bidder_alive=alive,
            endpoint=f"{bidder_url}/bidder",
            win_rate_pct=round(win_rate, 2),
            auctions_today=auctions_today,
            wins_today=wins_today,
            spend_today=round(float(spend_today), 4),
        )

    # ──────────────────────────────────────────────────────
    # Bid Multiplier расчёт
    # ──────────────────────────────────────────────────────

    @staticmethod
    def get_bid_multiplier(
        campaign_id: int,
        context: Dict[str, Any],
        db: Session,
    ) -> float:
        """
        Возвращает итоговый множитель ставки для данного контекста.
        Перемножает корректировки по всем применимым измерениям.

        context = {
            "country": "US",
            "device_type": "mobile",
            "os": "iOS",
            "hour": 14,          # UTC час
            "site_domain": "cnn.com"
        }
        """
        adjustments = db.query(BidAdjustment).filter(
            BidAdjustment.campaign_id == campaign_id
        ).all()

        multiplier = 1.0
        dimension_map = {
            "country": context.get("country", ""),
            "device": context.get("device_type", ""),
            "os": context.get("os", ""),
            "hour": str(context.get("hour", "")),
            "site": context.get("site_domain", ""),
        }

        for adj in adjustments:
            expected_value = dimension_map.get(adj.dimension)
            if expected_value and expected_value.lower() == adj.value.lower():
                multiplier *= adj.multiplier

        return max(0.1, min(multiplier, 5.0))  # ограничиваем диапазон 0.1x–5.0x
