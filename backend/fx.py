"""Курсы валют: ЦБ РФ -> fx_rates (история) + Redis-кэш.

Кабинет ведётся в рублях; закупка части трафика идёт в долларах.
Курс тянем с бесплатного зеркала ЦБ (cbr-xml-daily.ru), храним историю
в fx_rates, горячее значение кэшируем в Redis на сутки. Наценка к курсу
(fx_markup_percent, настройка аккаунта) применяется на чтении —
effective_rate = cbr_rate * (1 + markup/100).
"""
import json

import httpx

from cache import get_redis
from db import get_pool

CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
_CACHE_KEY = "fx:rates"
_CURRENCIES = ("USD", "EUR")


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
