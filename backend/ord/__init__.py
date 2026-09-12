"""ОРД (оператор рекламных данных) — адаптеры регистрации маркировки.

get_ord_adapter() возвращает OzonOrdClient когда задан ключ (БД или
ORD_OZON_API_KEY); без ключа — MockOrdAdapter только вне production
(детерминированные erid, без сети), в production — OrdNotConfigured.
"""
from .adapter import OrdAdapter, OrdError, OrdNotConfigured, get_ord_adapter  # noqa: F401
