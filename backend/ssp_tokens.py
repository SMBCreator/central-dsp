"""Shared-secret токены SSP-партнёров (заголовок X-CentralDSP-Token).

В БД хранится только hex(sha256(token)) (supply_partners.auth_token_hash,
миграция 032) и хвост для UI (auth_token_hint). Сверка — в константное время.
Go-биддер делает то же самое (bidder-go/internal/rtb/handler.go): sha256 от
заголовка и subtle.ConstantTimeCompare с auth_token_hash из фида.
"""

import hashlib
import hmac
import secrets

HEADER_NAME = "X-CentralDSP-Token"
_TOKEN_BYTES = 24  # token_urlsafe(24) -> 32 символа


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_hint(token: str) -> str:
    return token[-4:]


def verify_token(given: str | None, expected_hash: str | None) -> bool:
    """True, если хэш переданного токена совпадает с ожидаемым.

    Пустой expected_hash означает «токен партнёру не выдан» — проверка не
    требуется, вызывающая сторона решает сама (в rtb.py это = пропустить).
    """
    if not expected_hash:
        return True
    return hmac.compare_digest(hash_token(given or ""), expected_hash)
