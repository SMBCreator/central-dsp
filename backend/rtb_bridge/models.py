"""
SQLAlchemy модели для RTB-специфичных сущностей.
Добавляются к существующим моделям из backend/models.py.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, ARRAY, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from database import Base


class Advertiser(Base):
    """Рекламодатель — владелец бюджета, группирует кампании."""
    __tablename__ = "advertisers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    daily_budget = Column(Float, default=0.0)
    total_budget = Column(Float, default=0.0)
    currency = Column(String(3), default="USD")
    status = Column(String(20), default="active")  # active|paused|archived
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    creatives = relationship("Creative", back_populates="advertiser", lazy="dynamic")


class Creative(Base):
    """
    Рекламный материал: баннер / нативная реклама / видео.
    Прикреплён к кампании (campaign_id) и опционально к рекламодателю.
    """
    __tablename__ = "creatives"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    advertiser_id = Column(Integer, ForeignKey("advertisers.id", ondelete="SET NULL"), nullable=True)

    format = Column(String(20), nullable=False)     # banner | native | video
    width = Column(Integer, nullable=True)           # px, для баннеров
    height = Column(Integer, nullable=True)          # px, для баннеров

    # Banner fields
    image_url = Column(Text, nullable=True)
    click_url = Column(Text, nullable=True)

    # Native fields
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    icon_url = Column(Text, nullable=True)
    sponsored_by = Column(String(255), nullable=True)

    # Video fields
    vast_url = Column(Text, nullable=True)           # VAST XML URL
    video_url = Column(Text, nullable=True)          # прямая ссылка на видео
    duration_sec = Column(Integer, nullable=True)

    # Raw ad markup (HTML/VAST/native JSON) — если задан, используется как adm
    adm = Column(Text, nullable=True)

    status = Column(String(20), default="active")        # active|paused|archived
    audit_status = Column(String(20), default="pending") # pending|approved|rejected
    audit_feedback = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    advertiser = relationship("Advertiser", back_populates="creatives")


class TargetingRule(Base):
    """
    Правила таргетинга для RTB-кампании.
    Один к одному с кампанией (или один ко многим для A/B сплита).
    """
    __tablename__ = "targeting_rules"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"),
                         nullable=False, unique=True)  # 1 таргетинг на кампанию

    # Ставки
    bid_floor = Column(Float, default=0.0)           # минимальная ставка CPM
    bid_ceiling = Column(Float, default=10.0)         # максимальная ставка CPM

    # Гео
    geo_countries = Column(ARRAY(String), default=list)  # ["RU", "US", "DE"]

    # Устройства
    device_types = Column(ARRAY(String), default=list)   # ["mobile", "desktop", "tablet"]
    os_list = Column(ARRAY(String), default=list)         # ["iOS", "Android", "Windows"]
    browser_list = Column(ARRAY(String), default=list)    # ["Chrome", "Safari"]

    # Списки сайтов
    site_whitelist = Column(ARRAY(String), default=list)  # только эти домены
    site_blacklist = Column(ARRAY(String), default=list)  # исключить эти домены

    # Расписание ставок по часам
    # Формат: {"0": {"0": 1.0, "1": 0.8, ..., "23": 0.5}, "1": {...}, ...}
    # Внешний ключ: день недели (0=пн ... 6=вс), внутренний: час UTC
    # Значение: множитель ставки (1.0 = без изменений)
    hour_schedule = Column(JSONB, default=dict)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class AuctionLog(Base):
    """
    Лог каждого аукциона (победа или проигрыш).
    TimeSeries таблица — индексируется по created_at.
    """
    __tablename__ = "auction_logs"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    creative_id = Column(Integer, ForeignKey("creatives.id", ondelete="SET NULL"), nullable=True)

    # Идентификаторы аукциона
    auction_id = Column(String(64), nullable=False, index=True)  # BidRequest.id
    imp_id = Column(String(64), nullable=True)                   # Impression.id

    # Ценообразование
    bid_price = Column(Float, nullable=True)        # наша ставка
    win_price = Column(Float, nullable=True)         # цена победы (если выиграли)
    clearing_price = Column(Float, nullable=True)    # цена клиринга

    # Результат
    won = Column(Boolean, default=False)
    loss_reason = Column(Integer, nullable=True)     # OpenRTB loss reason code

    # Контекст
    site_domain = Column(String(255), nullable=True)
    app_bundle = Column(String(255), nullable=True)
    geo_country = Column(String(10), nullable=True)
    device_type = Column(String(20), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        index=True)  # критически важен для TimeSeries запросов

    __table_args__ = (
        # Составной индекс для аналитики по кампании за период
        Index("ix_auction_logs_campaign_created", "campaign_id", "created_at"),
        # Быстрый поиск по auction_id для Win/Loss notices
        Index("ix_auction_logs_auction_id", "auction_id"),
    )


class BidAdjustment(Base):
    """
    Корректировки ставок по срезам (гео, устройство, ОС, час, сайт).
    Celery задача обновляет их каждые 5 минут на основе статистики.
    """
    __tablename__ = "bid_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)

    dimension = Column(String(20), nullable=False)  # country|device|os|hour|site
    value = Column(String(100), nullable=False)      # "RU", "mobile", "iOS", "9", "site.com"
    multiplier = Column(Float, default=1.0)           # 0.5 = -50%, 1.5 = +50%

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_bid_adj_campaign_dimension", "campaign_id", "dimension", "value"),
    )
