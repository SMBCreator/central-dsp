"""Единые определения метрик для всех отчётов (audit E-01 / F-02).

Договор о представлении (F-02)
------------------------------
* В БД (`stats_hourly.spend`, `bid_events.clear_price`, `transactions`) расход
  хранится **net, в валюте платформы (RUB)** — курс применён один раз, на win.
  На чтении fx не применяется.
* В API `spend` = net × множитель наценки получателя. Множитель считается в
  одном месте (`routers.stats._resolve_scope`, для share/guest — snapshot на
  доступе) и применяется одним вызовом `present_spend`. Ни один отчёт не
  умножает второй раз и ни один не забывает умножить: контрактный тест
  `tests/test_stats_presentation.py` проверяет все представления.
* Money-метрики, производные от spend (cpm/ecpm, cpc, cpa, avg_bid,
  avg_bidfloor, avg_min_to_win), показываются в той же цене, что spend.
* Округление: деньги `MONEY_DECIMALS`, проценты `PCT_DECIMALS`, CTR
  `CTR_DECIMALS`. Производные метрики считает только `derive`.
* Итоги отчёта считаются отдельным запросом без LIMIT; строки за пределами
  LIMIT сворачиваются в `OTHER_LABEL`, чтобы `sum(rows) == totals` всегда.
  Отсутствующее значение измерения в событии — строка `NOT_SET_LABEL`, а не
  тире и не потеря конверсий.

Договор о расходе
-----------------
**spend = clear_price оплаченного события.** Оплаченное событие одно на бид
(`billing_core.bill_win`, ledger `transactions(bid_id) WHERE type='spend'`):

* CPM line item — событие `win`, `clear_price` в CPM (за 1000 показов),
  списывается `clear_price / 1000` в `win_notice`;
* CPC line item (audit R-03) — событие `click`, `clear_price` = цена клика в
  валюте, списывается в `click`; `win` такого бида пишется без `clear_price`
  и денег не несёт.

Туда же пишется `stats_hourly.spend`. Impression/viewable/video clearing не
несут — это события доставки, а не оплаты. Любой отчёт по сырым событиям
обязан считать расход через `SPEND_FROM_EVENTS_SQL`, иначе hourly-агрегат и
raw-отчёт расходятся (в raw был ноль при ненулевом hourly — E-01).

Тестовые события (`is_test`) в деньги не входят: фильтр `NOT is_test`
накладывает вызывающий SQL.
"""

# Алиас таблицы bid_events в запросе — подставляется в выражение.
# win — billing_event=win (nurl); billable — billing_event=burl (R-07); в одном
# биде clear_price несёт ровно одно из них (rtb._log_event), поэтому сумма не удваивает.
SPEND_FROM_EVENTS_SQL = (
    "(COALESCE(sum({e}.clear_price) FILTER (WHERE {e}.event_type IN ('win', 'billable')), 0) / 1000.0"
    " + COALESCE(sum({e}.clear_price) FILTER (WHERE {e}.event_type = 'click'), 0))"
)

# Billable-показы по журналу событий (R-07): billable (burl) либо win с
# деньгами (billing_event=win). Зеркало stats_hourly.billable_imps.
BILLABLE_FROM_EVENTS_SQL = (
    "count(*) FILTER (WHERE {e}.event_type = 'billable'"
    " OR ({e}.event_type = 'win' AND {e}.clear_price IS NOT NULL))"
)


def billable_expr(alias: str = "e") -> str:
    """SQL-выражение числа billable-показов по строкам bid_events с алиасом `alias`."""
    return BILLABLE_FROM_EVENTS_SQL.format(e=alias)


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


# ---------------------------------------------------------------------------
# Presentation layer (audit F-02)
# ---------------------------------------------------------------------------

from datetime import date as _date  # noqa: E402

MONEY_DECIMALS = 4   # spend, cpm, cpc, cpa, avg_*; UI форматирует до 2 знаков
PCT_DECIMALS = 2     # win_rate, viewability, loss_rate
CTR_DECIMALS = 3     # CTR обычно < 1 %, два знака его сплющат

MAX_HOURS = 8784         # 366 дней — верхняя граница любого `hours`
MAX_LIMIT = 5000         # верхняя граница любого `limit`
DEFAULT_LIMIT = 1000
MAX_RANGE_DAYS = 366

NOT_SET_LABEL = "Не указано"   # значение измерения отсутствует в событии-источнике
OTHER_LABEL = "Прочее"          # свёртка групп за пределами limit (значения известны)

ROW_KIND_DATA = "data"
ROW_KIND_NOT_SET = "not_set"
ROW_KIND_OTHER = "other"
# F-02/B14c: служебные строки уровня рекламодателя — конверсии, которые нельзя
# положить ни в одну строку измерения (нет бида / бид старше окна).
ROW_KIND_UNATTRIBUTED = "unattributed"
ROW_KIND_OUT_OF_WINDOW = "out_of_window"
UNATTRIBUTED_LABEL = "Без атрибуции"
OUT_OF_WINDOW_LABEL = "Вне окна атрибуции"
# Строки, для которых CPA не имеет смысла (spend и конверсии из разных множеств).
ROW_KINDS_WITHOUT_CPA = frozenset({ROW_KIND_NOT_SET, ROW_KIND_UNATTRIBUTED, ROW_KIND_OUT_OF_WINDOW})

# Покрытие атрибуции: доля конверсий владельца, которые можно положить в строку
# данного измерения (attributed И значение измерения не NULL). Ниже порога —
# CPA/CVR по этому срезу ненадёжны: авто-стоп/blacklist по conversion-KPI запрещён.
COVERAGE_LOW_THRESHOLD = 0.8
CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"
CONFIDENCE_NA = "n/a"          # конверсий нет вовсе — покрытие не определено
CONVERSION_KPI_METRICS = frozenset({"cpa", "cvr", "conversions"})

# Аддитивные метрики: суммируются в totals и вычитаются для строки «Прочее».
ADDITIVE_METRICS = ("bids", "wins", "impressions", "clicks", "viewable_imps", "billable_imps", "spend", "conversions", "losses")


def money(value) -> float | None:
    """Округление денежной величины; None проходит как None."""
    if value is None:
        return None
    return round(float(value), MONEY_DECIMALS)


def present_spend(net, mult: float) -> float:
    """Единственная точка применения наценки: net (из БД) → сумма для получателя."""
    return round(float(net or 0) * float(mult), MONEY_DECIMALS)


def pct(num, den, decimals: int = PCT_DECIMALS) -> float:
    return round(float(num) / float(den) * 100.0, decimals) if den else 0.0


def derive(*, bids=0, wins=0, impressions=0, clicks=0, viewable_imps=0, spend=0.0,
           conversions=None, losses=None, billable_imps=0) -> dict:
    """Производные метрики из аддитивных. `spend` уже в цене получателя.

    conversions=None — конверсии для этого среза недоступны (cpa=None),
    0 — известно, что их не было (cpa=0.0). losses=None — источник их не
    отдаёт (hourly), loss_rate не считается.

    R-07: wins (выигрыши), impressions (доставка по пикселю) и billable_imps
    (показы с расходом) — три разных числа. cpm — по доставленным показам
    (как раньше); ecpm — spend / billable_imps × 1000, т.е. фактическая цена
    оплаченного показа; без billable-показов ecpm совпадает с cpm.
    """
    imps = int(impressions or 0)
    clk = int(clicks or 0)
    b = int(bids or 0)
    sp = float(spend or 0)
    out = {
        "win_rate": pct(wins or 0, b),
        "ctr": pct(clk, imps, CTR_DECIMALS),
        "cpm": round(sp / imps * 1000.0, MONEY_DECIMALS) if imps else 0.0,
        "cpc": round(sp / clk, MONEY_DECIMALS) if clk else 0.0,
        "viewability": pct(viewable_imps or 0, imps),
    }
    billable = int(billable_imps or 0)
    out["ecpm"] = round(sp / billable * 1000.0, MONEY_DECIMALS) if billable else out["cpm"]
    if conversions is None:
        out["cpa"] = None
    else:
        out["cpa"] = round(sp / int(conversions), MONEY_DECIMALS) if conversions else 0.0
    if losses is not None:
        # доля проигрышей — от откликов, по которым SSP вообще шлёт lurl
        out["loss_rate"] = pct(losses, b) if losses else 0.0
    return out


def coverage_ratio(covered, total) -> float | None:
    """attributed-с-измерением / все конверсии владельца. None, если конверсий нет."""
    t = int(total or 0)
    if t <= 0:
        return None
    return round(min(int(covered or 0), t) / t, 4)


def coverage_confidence(ratio: float | None) -> str:
    if ratio is None:
        return CONFIDENCE_NA
    return CONFIDENCE_LOW if ratio < COVERAGE_LOW_THRESHOLD else CONFIDENCE_HIGH


def conversion_kpi_trustworthy(attributed, total) -> bool:
    """Можно ли принимать автоматические решения (stop/blacklist) по CPA/CVR/conversions.

    Нет конверсий вовсе — покрытие не определено, решение остаётся за правилом
    (у него свои пороги по показам/spend). Есть конверсии, но атрибутировано
    < COVERAGE_LOW_THRESHOLD — трекинг clid неисправен, KPI по конверсиям врут.
    """
    ratio = coverage_ratio(attributed, total)
    return ratio is None or ratio >= COVERAGE_LOW_THRESHOLD


def clamp_hours(hours) -> int:
    try:
        h = int(hours)
    except (TypeError, ValueError):
        h = 24
    return max(1, min(h, MAX_HOURS))


def clamp_limit(limit, default: int = DEFAULT_LIMIT) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, MAX_LIMIT))


class DateRangeError(ValueError):
    """Диапазон отчёта не принят; message пригоден для 422."""


def parse_date_range(date_from, date_to) -> tuple[_date, _date]:
    """Строго ISO YYYY-MM-DD, from <= to (без молчаливого swap), не длиннее года."""
    if not (date_from and date_to):
        raise DateRangeError("Нужны обе даты: date_from и date_to")
    try:
        d_from, d_to = _date.fromisoformat(str(date_from)), _date.fromisoformat(str(date_to))
    except ValueError:
        raise DateRangeError("Даты в формате YYYY-MM-DD")
    if d_from > d_to:
        raise DateRangeError("date_from позже date_to")
    if (d_to - d_from).days > MAX_RANGE_DAYS:
        raise DateRangeError("Диапазон не больше года")
    return d_from, d_to
