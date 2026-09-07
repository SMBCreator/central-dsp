"""Shared-secret токены SSP-партнёров (заголовок X-CentralDSP-Token).

В БД хранится только hex(sha256(token)) (supply_partners.auth_token_hash,
миграция 032) и хвост для UI (auth_token_hint). Сверка — в константное время.
Go-биддер делает то же самое (bidder-go/internal/rtb/partner_auth.go): sha256 от
заголовка и subtle.ConstantTimeCompare с auth_token_hash из фида.

Audit S-06 (миграция 039): пустой хэш = партнёр НЕ авторизован (fail-closed),
а не «проверка не требуется». Ротация безразрывна: предыдущий хэш принимается
до token_prev_valid_until (PREV_TOKEN_GRACE).
"""

import hashlib
import hmac
import ipaddress
import secrets
from datetime import datetime, timedelta, timezone

HEADER_NAME = "X-CentralDSP-Token"
_TOKEN_BYTES = 24  # token_urlsafe(24) -> 32 символа

# Окно, в котором после ротации принимается и старый токен (SSP меняет секрет
# не мгновенно). Согласовано с Go: он читает token_prev_valid_until из фида.
PREV_TOKEN_GRACE = timedelta(minutes=15)


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_hint(token: str) -> str:
    return token[-4:]


def verify_token(
    given: str | None,
    expected_hash: str | None,
    prev_hash: str | None = None,
    prev_valid_until: datetime | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """True, если sha256(given) совпадает с текущим хэшем или — в окне ротации —
    с предыдущим.

    Пустой expected_hash = токен не выдан/отозван -> False всегда (S-06).
    Пустой given -> False. Сравнение в константное время.
    """
    if not expected_hash or not given:
        return False
    got = hash_token(given)
    if hmac.compare_digest(got, expected_hash):
        return True
    if prev_hash and prev_valid_until is not None:
        now = now or datetime.now(timezone.utc)
        if prev_valid_until.tzinfo is None:
            prev_valid_until = prev_valid_until.replace(tzinfo=timezone.utc)
        if now < prev_valid_until and hmac.compare_digest(got, prev_hash):
            return True
    return False


def validate_ip_whitelist(items: list[str]) -> list[str]:
    """Нормализует ip_whitelist: каждый элемент — IP или CIDR.

    Возвращает список канонических строк: одиночный IP остаётся IP
    (`203.0.113.7`), сеть — CIDR с нормализованным адресом (`10.1.0.0/16`,
    `2001:db8::/32`). Невалидный элемент -> ValueError с его текстом —
    роутер отдаёт 422. Раньше мусор молча пропускался и в Go мог отключить
    фильтр целиком; теперь такое просто не сохраняется.
    """
    out: list[str] = []
    for raw in items:
        s = str(raw).strip()
        if not s:
            continue
        try:
            if "/" in s:
                out.append(str(ipaddress.ip_network(s, strict=False)))
            else:
                out.append(str(ipaddress.ip_address(s)))
        except ValueError as exc:
            raise ValueError(f"Некорректный IP/CIDR в whitelist: «{s}»") from exc
    return out
