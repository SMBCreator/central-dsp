"""Fail-fast проверки конфигурации при старте процесса.

Чеклист 6.1 / 6.11: пустой `CRON_SECRET` открывает `/cron/reset-daily`
любому снаружи, публичный dev-fallback `EVENT_TOKEN_SECRET` делает
win/imp/click-токены подделываемыми, `RTB_API_TOKEN` защищает фид биддера.
В production такие процессы не должны подниматься вообще — лучше упасть на
старте с понятным сообщением, чем торговать с открытыми ручками.

Вне production (dev, тесты, песочница) проверки только логируются.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

log = logging.getLogger("startup_checks")

MIN_SECRET_LEN = 32

# Секреты, без которых production небезопасен. (name, purpose)
REQUIRED_SECRETS: tuple[tuple[str, str], ...] = (
    ("EVENT_TOKEN_SECRET", "подписывает win/imp/click event-токены (nurl/lurl)"),
    ("CRON_SECRET", "закрывает /cron/* (reset-daily, retention, ord-export)"),
    ("RTB_API_TOKEN", "закрывает фид биддера /rtb-feed/*"),
)

_PLACEHOLDER_PREFIXES = ("change_me", "changeme", "change-me", "replace_me", "todo", "xxx")
_WEAK_VALUES = frozenset(
    {"dev", "test", "secret", "password", "postgres", "admin", "root", "123456", "centraldsp"}
)


def is_production(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("APP_ENV", "").strip().lower() in ("production", "prod")


def _secret_problem(name: str, value: str | None) -> str | None:
    if not value or not value.strip():
        return f"{name} не задан"
    v = value.strip()
    low = v.lower()
    if low.startswith(_PLACEHOLDER_PREFIXES):
        return f"{name} — плейсхолдер из .env.example ({v[:12]}…)"
    if low in _WEAK_VALUES:
        return f"{name} — известное слабое значение"
    if len(v) < MIN_SECRET_LEN:
        return f"{name} короче {MIN_SECRET_LEN} символов (len={len(v)}); сгенерировать: openssl rand -hex 32"
    return None


def _database_url_problem(url: str | None) -> str | None:
    if not url:
        return "DATABASE_URL не задан"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "DATABASE_URL не парсится"
    pw = (parts.password or "").lower()
    if not pw:
        return None  # trust/peer-auth или секрет в другом месте — не наша зона
    if pw in _WEAK_VALUES or pw.startswith(_PLACEHOLDER_PREFIXES):
        return "DATABASE_URL: пароль Postgres — дефолтный/плейсхолдер"
    return None


def collect_problems(env: Mapping[str, str] | None = None) -> list[str]:
    """Список нарушений. Пустой список — конфигурация пригодна для production."""
    env = os.environ if env is None else env
    problems: list[str] = []
    for name, purpose in REQUIRED_SECRETS:
        value = env.get(name)
        if name == "EVENT_TOKEN_SECRET" and not value:
            # tokens.py принимает SESSION_SECRET как fallback — зеркалим.
            value = env.get("SESSION_SECRET")
        p = _secret_problem(name, value)
        if p:
            problems.append(f"{p} — {purpose}")
    p = _database_url_problem(env.get("DATABASE_URL"))
    if p:
        problems.append(p)
    return problems


def collect_warnings(env: Mapping[str, str] | None = None) -> list[str]:
    """Не фатально, но в production стоит знать."""
    env = os.environ if env is None else env
    warnings: list[str] = []
    if env.get("REGISTRATION_MODE", "").strip().lower() == "open":
        warnings.append("REGISTRATION_MODE=open — любой может создать аккаунт (чеклист 6.2)")
    if env.get("RTB_PYTHON_SERVING", "").strip().lower() in ("1", "true", "yes", "on"):
        warnings.append("RTB_PYTHON_SERVING включён — Python-биддер торгует параллельно Go (двойные биды)")
    if env.get("ALLOW_MOCK_FALLBACK", "").strip() == "1":
        warnings.append("ALLOW_MOCK_FALLBACK=1 — прокси фронта отдаёт мок-данные при недоступном API")
    return warnings


def assert_production_config(env: Mapping[str, str] | None = None) -> None:
    """Вызывать на старте процесса. В production при нарушениях — RuntimeError."""
    env = os.environ if env is None else env
    problems = collect_problems(env)
    warnings = collect_warnings(env)
    prod = is_production(env)
    for w in warnings:
        log.warning("config: %s", w)
    if not problems:
        return
    if prod:
        raise RuntimeError(
            "Небезопасная конфигурация для APP_ENV=production:\n  - " + "\n  - ".join(problems)
        )
    for p in problems:
        log.warning("config (dev, не фатально): %s", p)
