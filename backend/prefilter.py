"""Throttling / предфильтрация входящего SSP-трафика (Часть C, дешёвая защита hot path).

Отсекает заведомо бесперспективные bid-запросы ДО полного цикла обработки
(Redis budget/freq → Postgres candidates → таргетинг → аукцион), экономя
infra-ресурсы. Это НЕ бизнес-логика качества трафика (clean traffic, Часть B) —
только O(1)-проверки над уже распарсенным JSON.

C1d: hot path обслуживает Go (bidder-go/internal/prefilter, метрика
bidder_prefilter_drops_total). Этот модуль — эталон для parity-тестов и владелец
админ-API списка datacenter-подсетей (Redis SET prefilter:dc_subnets общий с Go).

Причины отсечения (bounded enum, те же строки, что в Go):
  bot_ua        — User-Agent содержит паттерн бота/краулера/CLI-клиента
  geo_unserved  — страна ЯВНО указана и не входит в THROTTLE_COUNTRIES
  no_device_id  — нет ни device.ifa, ни device.ip/ipv6 (нечем считать freq cap)
  datacenter_ip — IP попадает в известную datacenter-подсеть (Redis SET)

Принципы:
  - fail-open: любая ошибка/недоступность Redis/неизвестное значение → запрос ПРОПУСКАЕТСЯ;
  - никаких запросов к Postgres; Redis — только фоновое обновление кэша подсетей
    по паттерну single-flight (как _hot_cache в rtb.py);
  - конфигурация через env: THROTTLE_ENABLED (default on), THROTTLE_COUNTRIES
    (csv, default "ru"; пусто = гео-фильтр выключен), THROTTLE_REQUIRE_DEVICE_ID
    (default on).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time

logger = logging.getLogger("prefilter")

# ---------------------------------------------------------------- config ---

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


THROTTLE_ENABLED = _env_bool("THROTTLE_ENABLED", True)
THROTTLE_REQUIRE_DEVICE_ID = _env_bool("THROTTLE_REQUIRE_DEVICE_ID", True)
# csv стран (ISO alpha-2/alpha-3, регистронезависимо); пустая строка = гео-фильтр выключен
_countries_raw = os.environ.get("THROTTLE_COUNTRIES", "ru,rus")
THROTTLE_COUNTRIES: frozenset[str] = frozenset(
    c.strip().lower() for c in _countries_raw.split(",") if c.strip()
)

# Паттерны ботов/краулеров/CLI-клиентов. Пустой UA НЕ блокируем: часть мобильного
# in-app инвентаря легитимно приходит без UA.
_BOT_UA_RE = re.compile(
    r"bot|crawler|spider|scraper|curl|wget|python-requests|python-urllib|go-http-client"
    r"|java/|libwww|httpclient|okhttp|headless|phantomjs|selenium|puppeteer|playwright",
    re.IGNORECASE,
)

# ------------------------------------------------- datacenter subnet cache ---
# Redis SET prefilter:dc_subnets хранит префиксы CIDR вида "1.2.3." (по /24)
# и "1.2." (по /16) — проверка сводится к str.startswith, без парсинга CIDR
# на hot path. Кэшируется in-process с single-flight обновлением.

DC_SUBNETS_KEY = "prefilter:dc_subnets"
_DC_CACHE_TTL = float(os.environ.get("THROTTLE_DC_CACHE_TTL", "60"))

_dc_cache: tuple[frozenset[str], float] | None = None  # (prefixes, expires_at)
_dc_refreshing = False


def _dc_prefixes() -> frozenset[str]:
    """Актуальный набор datacenter-префиксов; пустой набор пока кэш не прогрет.

    Никогда не блокирует hot path: при устаревании кэша отдаёт stale-значение
    и запускает фоновое обновление (single-flight).
    """
    global _dc_refreshing
    now = time.monotonic()
    if _dc_cache is not None:
        prefixes, expires = _dc_cache
        if now < expires:
            return prefixes
        # stale: используем старое значение, обновляем в фоне
        if not _dc_refreshing:
            _dc_refreshing = True
            try:
                asyncio.get_running_loop().create_task(_refresh_dc_cache())
            except RuntimeError:
                _dc_refreshing = False
        return prefixes
    # холодный старт: запускаем прогрев, текущий запрос пропускаем (fail-open)
    if not _dc_refreshing:
        _dc_refreshing = True
        try:
            asyncio.get_running_loop().create_task(_refresh_dc_cache())
        except RuntimeError:
            _dc_refreshing = False
    return frozenset()


async def _refresh_dc_cache() -> None:
    global _dc_cache, _dc_refreshing
    try:
        from cache import get_redis

        r = get_redis()
        members = await r.smembers(DC_SUBNETS_KEY)
        _dc_cache = (frozenset(members or ()), time.monotonic() + _DC_CACHE_TTL)
    except Exception as exc:  # Redis недоступен — fail-open, кэш не трогаем
        logger.warning("dc subnet cache refresh failed: %s", exc)
        if _dc_cache is not None:
            # продлеваем stale, чтобы не долбить Redis каждым запросом
            _dc_cache = (_dc_cache[0], time.monotonic() + _DC_CACHE_TTL)
    finally:
        _dc_refreshing = False


def _reset_dc_cache_for_tests() -> None:
    global _dc_cache, _dc_refreshing
    _dc_cache = None
    _dc_refreshing = False


def _ip_matches_dc(ip: str, prefixes: frozenset[str]) -> bool:
    """Проверка по startswith: префиксы каноничны ("1.2.3." или "1.2.")."""
    if not ip or not prefixes:
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False  # IPv6 в datacenter-списке пока не поддерживаем
    p24 = f"{parts[0]}.{parts[1]}.{parts[2]}."
    p16 = f"{parts[0]}.{parts[1]}."
    return p24 in prefixes or p16 in prefixes


def cidr_to_prefix(cidr: str) -> str | None:
    """Валидирует CIDR /16 или /24 и приводит к каноничному префиксу для SET.

    "45.155.204.0/24" -> "45.155.204." ; "45.155.0.0/16" -> "45.155."
    Иные маски/некорректный ввод -> None (admin API вернёт 422).
    """
    cidr = (cidr or "").strip()
    m = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(16|24)", cidr)
    if not m:
        return None
    octets = [int(m.group(i)) for i in range(1, 5)]
    if any(o > 255 for o in octets):
        return None
    if m.group(5) == "24":
        return f"{octets[0]}.{octets[1]}.{octets[2]}."
    return f"{octets[0]}.{octets[1]}."


# ------------------------------------------------------------------ check ---

def check(req: dict) -> str | None:
    """Возвращает причину отсечения (bounded enum) либо None (пропустить дальше).

    Вызывается в bid() ПОСЛЕ parse JSON и ДО process_bid_request. Только
    синхронные O(1)-операции; никакого I/O.
    """
    if not THROTTLE_ENABLED:
        return None
    try:
        device = req.get("device") or {}

        # 1. Бот/краулер UA (самая дешёвая проверка — первой)
        ua = device.get("ua") or ""
        if ua and _BOT_UA_RE.search(ua):
            return "bot_ua"

        # 2. Гео вне обслуживаемых стран (только если страна явно указана)
        if THROTTLE_COUNTRIES:
            country = ((device.get("geo") or {}).get("country") or "").strip().lower()
            if country and country not in THROTTLE_COUNTRIES:
                return "geo_unserved"

        # 3. Нет идентификатора устройства
        if THROTTLE_REQUIRE_DEVICE_ID:
            if not (device.get("ifa") or device.get("ip") or device.get("ipv6")):
                return "no_device_id"

        # 4. Datacenter-подсеть (кэш из Redis; пустой кэш = пропуск)
        ip = device.get("ip") or ""
        if ip and _ip_matches_dc(ip, _dc_prefixes()):
            return "datacenter_ip"
    except Exception as exc:  # никакая ошибка предфильтра не должна ронять hot path
        logger.warning("prefilter check failed (fail-open): %s", exc)
        return None
    return None
