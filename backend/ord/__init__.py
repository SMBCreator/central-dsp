"""ОРД (оператор рекламных данных) — адаптеры регистрации маркировки.

get_ord_adapter() возвращает OzonOrdClient когда задан ORD_OZON_API_KEY,
иначе MockOrdAdapter (dev/тесты): детерминированные erid, без сети.
"""
from .adapter import OrdAdapter, OrdError, get_ord_adapter  # noqa: F401
