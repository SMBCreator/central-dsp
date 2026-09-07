"""Единые определения метрик для всех отчётов (audit E-01 / F-02).

Договор о расходе
-----------------
**spend = clearing price оплаченного win.** Деньги списываются в `win_notice`
(`billing_core.bill_win`) по `clear_price`, туда же пишется `stats_hourly.spend`
и событие `win` с `clear_price`. Impression/click clearing не несут — это
события доставки, а не оплаты. Любой отчёт по сырым событиям обязан считать
расход через `SPEND_FROM_EVENTS_SQL`, иначе hourly-агрегат и raw-отчёт
расходятся (в raw был ноль при ненулевом hourly — E-01).

Тестовые события (`is_test`) в деньги не входят: фильтр `NOT is_test`
накладывает вызывающий SQL.
"""

# Алиас таблицы bid_events в запросе — подставляется в выражение.
SPEND_FROM_EVENTS_SQL = "COALESCE(sum({e}.clear_price) FILTER (WHERE {e}.event_type = 'win'), 0) / 1000.0"


def spend_expr(alias: str = "e") -> str:
    """SQL-выражение расхода (в валюте) по строкам bid_events с алиасом `alias`."""
    return SPEND_FROM_EVENTS_SQL.format(e=alias)


# Столбцы bid_events, наследуемые win/imp/click/view/loss от исходного бида
# (через bidmeta). Группировки «по площадкам» опираются на них.
INVENTORY_DIMENSIONS = (
    "domain", "bundle", "publisher_id", "tagid",
    "geo_country", "geo_region", "geo_city",
    "device_type", "os", "browser", "ad_type",
)
