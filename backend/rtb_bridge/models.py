"""
rtb_bridge/models.py — НЕ содержит SQLAlchemy моделей.

Все RTB-модели (Creative, TargetingRule, AuctionLog, BidAdjustment,
Advertiser и т.д.) определены в backend/models.py.

Этот файл существует только для удобства импорта внутри пакета rtb_bridge:
  from rtb_bridge.models import Creative, TargetingRule, AuctionLog, BidAdjustment

Дублирование классов с одинаковым __tablename__ вызывало:
  sqlalchemy.exc.InvalidRequestError: Table 'creatives' is already defined
  for this MetaData instance.

Решение: импортируем из единственного источника истины — backend/models.py.
"""
from models import (
    Creative,
    TargetingRule,
    AuctionLog,
    BidAdjustment,
)

__all__ = [
    "Creative",
    "TargetingRule",
    "AuctionLog",
    "BidAdjustment",
]
