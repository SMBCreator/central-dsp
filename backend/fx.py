"""Курсы валют: ЦБ РФ -> fx_rates (история) + Redis-кэш.

Кабинет ведётся в рублях; закупка части трафика идёт в долларах.
Курс тянем с бесплатного зеркала ЦБ (cbr-xml-daily.ru), храним историю
в fx_rates, горячее значение кэшируем в Redis на сутки. Наценка к курсу
(fx_markup_percent, настройка аккаунта) применяется на чтении —
effective_rate = cbr_rate * (1 + markup/100).

Договор валюты в аукционе (audit R-03, паритет с bidder-go internal/fx):
  * все внутренние деньги — RUB: цена кандидата, резерв, бюджеты, ledger;
  * валюта ответа — choose_currency(req.cur, валюта расчётов партнёра);
  * bidfloor запроса — в imp.bidfloorcur, иначе в req.cur[0], иначе в
    валюте партнёра; bid_floor партнёра — в его валюте расчётов;
  * один effective_rate на (валюта, наценка владельца кампании) применяется
    ко всему биду: floor -> RUB (×), цена RUB -> валюта ответа (÷),
    ${AUCTION_PRICE} -> RUB (×) при win. Наценка — буфер волатильности:
    ставка в валюте ниже, floor и списание в рублях выше;
  * нет курса для нужной валюты — no-bid, а не ставка «как будто в рублях».
"""
import json
import time

import httpx

from cache import get_redis
from db import get_pool

CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
_CACHE_KEY = "fx:rates"
_CURRENCIES = ("USD", "EUR")

RUB = "RUB"
DEFAULT_MARKUP_PERCENT = 3.0  # = routers.settings.DEFAULTS["fx_markup_percent"]


def choose_currency(req_cur, partner_currency) -> str | None:
    """Валюта ответа. req.cur пуст -> валюта расчётов партнёра. Иначе валюта
    партнёра, если SSP её принимает; RUB, если принимает рубли; иначе None
    (no-bid: платформа не выставляет цену в валюте, в которой не рассчитывается)."""
    pc = (partner_currency or RUB).upper()
    allowed = [str(c).upper() for c in (req_cur or []) if c]
    if not allowed:
        return pc
    if pc in allowed:
        return pc
    if RUB in allowed:
        return RUB
    return None


def floor_currency(bidfloorcur, req_cur, partner_currency) -> str:
    """Валюта imp.bidfloor: bidfloorcur -> req.cur[0] -> валюта партнёра."""
    if bidfloorcur:
        return str(bidfloorcur).upper()
    allowed = [str(c).upper() for c in (req_cur or []) if c]
    if allowed:
        return allowed[0]
    return (partner_currency or RUB).upper()


def effective_rate(cur, rates, markup_percent) -> float | None:
    """RUB за единицу cur с наценкой владельца кампании. RUB -> 1.0.
    Нет курса (или курс <= 0) -> None: бид в этой валюте невозможен."""
    cur = (cur or RUB).upper()
    if cur == RUB:
        return 1.0
    raw = (rates or {}).get(cur)
    try:
        r = float(raw)
    except (TypeError, ValueError):
        return None
    if r <= 0:
        return None
    m = DEFAULT_MARKUP_PERCENT if markup_percent is None else float(markup_percent)
    return r * (1 + m / 100)


_snapshot: tuple[float, dict] | None = None
_SNAPSHOT_TTL = 60.0


async def rates_snapshot() -> dict:
    """Курсы ЦБ для hot path биддера: in-process (60 с) -> Redis -> БД.
    Без живого fetch и без исключений: недоступный курс = {} -> no-bid по
    валютным бидам, рублёвые не страдают."""
    global _snapshot
    now = time.monotonic()
    if _snapshot and now - _snapshot[0] < _SNAPSHOT_TTL:
        return _snapshot[1]
    rates: dict = {}
    try:
        cached = await get_redis().get(_CACHE_KEY)
        if cached:
            rates = {k: float(v) for k, v in json.loads(cached).items()}
    except Exception:
        rates = {}
    if not rates:
        try:
            pool = await get_pool()
            rows = await pool.fetch(
                """SELECT DISTINCT ON (currency) currency, rate
                   FROM fx_rates ORDER BY currency, rate_date DESC"""
            )
            rates = {r["currency"]: float(r["rate"]) for r in rows}
        except Exception:
            rates = {}
    _snapshot = (now, rates)
    return rates


def set_rates_for_tests(rates: dict | None) -> None:
    global _snapshot
    _snapshot = None if rates is None else (time.monotonic(), dict(rates))


async def refresh_rates() -> dict:
    """Тянет дневной курс ЦБ и сохраняет в БД + Redis. Вызывается кроном."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(CBR_URL)
        resp.raise_for_status()
        data = resp.json()
    pool = await get_pool()
    out: dict = {}
    for code in _CURRENCIES:
        v = data["Valute"][code]
        rate = float(v["Value"]) / float(v.get("Nominal") or 1)
        await pool.execute(
            """INSERT INTO fx_rates (currency, rate_date, rate)
               VALUES ($1, CURRENT_DATE, $2)
               ON CONFLICT (currency, rate_date)
                 DO UPDATE SET rate = EXCLUDED.rate, fetched_at = now()""",
            code, rate,
        )
        out[code] = rate
    r = get_redis()
    await r.set(_CACHE_KEY, json.dumps(out), ex=86400)
    return out


async def get_rate(currency: str = "USD") -> float | None:
    """Курс ЦБ без наценки: Redis -> БД (последний известный) -> живой fetch."""
    r = get_redis()
    cached = await r.get(_CACHE_KEY)
    if cached:
        rates = json.loads(cached)
        if currency in rates:
            return float(rates[currency])
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT rate FROM fx_rates WHERE currency = $1 ORDER BY rate_date DESC LIMIT 1",
        currency,
    )
    if row:
        return float(row["rate"])
    try:
        rates = await refresh_rates()
        return rates.get(currency)
    except Exception:
        return None
