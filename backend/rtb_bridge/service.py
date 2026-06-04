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
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
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
        Возвращает активные кампании в формате, понятном Java-биддеру.
        Фильтрует по:
        - status == 'active'
        - бюджет не исчерпан
        - есть хотя бы один одобренный активный креатив
        """
        campaigns = db.query(Campaign).filter(
            Campaign.status == "active"
        ).all()

        result = []
        for camp in campaigns:
            # Проверяем бюджет
            if not RTBService._has_budget(camp, db):
                continue

            # Таргетинг
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

            # Активные одобренные креативы
            creatives = db.query(Creative).filter(
                Creative.campaign_id == camp.id,
                Creative.status == "active",
                Creative.audit_status == "approved",
            ).all()

            # Пропускаем кампании без сервируемых креативов
            if not creatives:
                continue

            creatives_out = [
                CreativeOut(
                    id=c.id,
                    format=c.format,
                    width=c.width,
                    height=c.height,
                    image_url=c.image_url,
                    click_url=c.click_url,
                    title=c.title,
                    description=c.description,
                    icon_url=c.icon_url,
                    sponsored_by=c.sponsored_by,
                    vast_url=c.vast_url,
                    video_url=c.video_url,
                    duration_sec=c.duration_sec,
                    adm=c.adm,
                    adomain=getattr(c, "adomain", None),
                )
                for c in creatives
            ]

            # Остаток бюджета
            budget_remaining = RTBService._get_budget_remaining(camp, db)

            # Bid floor/ceiling: приоритет — поля кампании,
            # fallback — targeting rule.
            bid_floor = getattr(camp, "bid_floor", None)
            bid_ceiling = getattr(camp, "bid_ceiling", None)
            if bid_floor is None and targeting_rule:
                bid_floor = targeting_rule.bid_floor
            if bid_ceiling is None and targeting_rule:
                bid_ceiling = targeting_rule.bid_ceiling

            result.append(RTBCampaignOut(
                id=camp.id,
                name=camp.name,
                status=camp.status,
                bid_price=getattr(camp, "bid_price", None),
                bid_floor=bid_floor,
                bid_ceiling=bid_ceiling,
                daily_budget=camp.daily_budget,
                total_budget=camp.total_budget,
                budget_remaining=budget_remaining,
                click_url=getattr(camp, "click_url", None),
                tracking_pixel=getattr(camp, "tracking_pixel", None),
                freq_cap=getattr(camp, "freq_cap", None),
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
    # Win / Loss Notice
    # ──────────────────────────────────────────────────────

    @staticmethod
    def process_win_notice(notice: WinNotice, db: Session) -> AuctionLog:
        """
        Биддер выиграл аукцион.
        1. Записываем AuctionLog(won=True, bid_price=..., win_price=...)
        2. Создаём Event(event_name='impression') для attribution chain
        """
        log = AuctionLog(
            campaign_id=notice.campaign_id,
            creative_id=notice.creative_id,
            auction_id=notice.auction_id,
            imp_id=notice.imp_id or None,
            bid_price=notice.bid_price,           # наша ставка (может быть None)
            win_price=notice.win_price,
            clearing_price=notice.clearing_price or notice.win_price,
            won=True,
            site_domain=notice.site_domain,
            app_bundle=notice.app_bundle,
            geo_country=notice.geo_country,
            device_type=notice.device_type,
        )
        db.add(log)

        # Attribution: impression event привязан к auction_id
        impression_event = Event(
            pixel_id=None,
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
                "bid_price_cpm": notice.bid_price,
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
            f"bid={notice.bid_price} win={notice.win_price:.4f} site={notice.site_domain}"
        )
        return log

    @staticmethod
    def process_loss_notice(notice: LossNotice, db: Session) -> AuctionLog:
        """
        Биддер проиграл аукцион.
        Записываем AuctionLog(won=False, bid_price=...) для расчёта win rate
        и avg bid price в статистике.
        """
        log = AuctionLog(
            campaign_id=notice.campaign_id,
            creative_id=notice.creative_id,
            auction_id=notice.auction_id,
            imp_id=notice.imp_id or None,
            bid_price=notice.bid_price,   # наша ставка — для avg_bid_price в stats
            won=False,
            loss_reason=notice.loss_reason,
        )
        db.add(log)
        db.commit()
        db.refresh(log)

        logger.debug(
            f"[LOSS] campaign={notice.campaign_id} bid={notice.bid_price} "
            f"reason={notice.loss_reason} auction={notice.auction_id}"
        )
        return log

    # ──────────────────────────────────────────────────────
    # Статистика
    # ──────────────────────────────────────────────────────

    @staticmethod
    def get_campaign_stats(campaign_id: int, db: Session) -> RTBStatsOut:
        """RTB статистика кампании за всё время."""
        auctions = db.query(func.count(AuctionLog.id)).filter(
            AuctionLog.campaign_id == campaign_id
        ).scalar() or 0

        wins = db.query(func.count(AuctionLog.id)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.won == True,
        ).scalar() or 0

        # avg_bid_price учитывает все аукционы (и выигранные, и проигранные)
        # где bid_price сохранён (не NULL)
        avg_bid = db.query(func.avg(AuctionLog.bid_price)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.bid_price.isnot(None),
        ).scalar()

        avg_win = db.query(func.avg(AuctionLog.win_price)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.won == True,
        ).scalar()

        total_spend = db.query(func.sum(AuctionLog.win_price)).filter(
            AuctionLog.campaign_id == campaign_id,
            AuctionLog.won == True,
        ).scalar()

        losses = auctions - wins
        win_rate = round((wins / auctions * 100), 2) if auctions else 0.0
        avg_cpm = round(float(avg_win or 0.0), 4)

        return RTBStatsOut(
            campaign_id=campaign_id,
            auctions=auctions,
            wins=wins,
            losses=losses,
            win_rate_pct=win_rate,
            avg_bid_price=round(float(avg_bid or 0.0), 4),
            avg_win_price=round(float(avg_win or 0.0), 4),
            total_spend=round(float(total_spend or 0.0), 4),
            avg_cpm=avg_cpm,
        )

    # ──────────────────────────────────────────────────────
    # Статус биддера
    # ──────────────────────────────────────────────────────

    @staticmethod
    async def get_bidder_status(db: Session) -> BidderStatusOut:
        """Проверяет доступность Java-биддера по HTTP."""
        bidder_url = getattr(settings, "BIDDER_URL", "http://bidder:8080")
        alive = False

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{bidder_url}/health")
                alive = resp.status_code == 200
        except Exception as e:
            logger.warning(f"[BIDDER STATUS] Cannot reach bidder: {e}")

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

        win_rate = round((wins_today / auctions_today * 100), 2) if auctions_today else 0.0

        return BidderStatusOut(
            bidder_alive=alive,
            endpoint=f"{bidder_url}/bidder",
            win_rate_pct=win_rate,
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
        Итоговый множитель ставки для данного контекста показа.
        Перемножает корректировки по всем применимым измерениям.

        context = {
            "country": "US",
            "device_type": "mobile",
            "os": "iOS",
            "hour": 14,
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
            expected = dimension_map.get(adj.dimension, "")
            if expected and expected.lower() == adj.value.lower():
                multiplier *= adj.multiplier

        # Ограничиваем диапазон: 0.1x – 5.0x
        return max(0.1, min(multiplier, 5.0))
