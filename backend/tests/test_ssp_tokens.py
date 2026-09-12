"""Хэширование и сверка токенов SSP-партнёров (backend/ssp_tokens.py, миграции 032/039)."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

import ssp_tokens


def test_generate_token_is_random_and_urlsafe():
    a, b = ssp_tokens.generate_token(), ssp_tokens.generate_token()
    assert a != b
    assert len(a) >= 32
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_matches_postgres_backfill_formula():
    # Миграция 032: encode(sha256(convert_to(auth_token,'UTF8')),'hex')
    tok = "csp_example_token_d7e8"
    assert ssp_tokens.hash_token(tok) == hashlib.sha256(tok.encode("utf-8")).hexdigest()
    assert ssp_tokens.token_hint(tok) == "d7e8"


def test_verify_token_semantics():
    tok = ssp_tokens.generate_token()
    h = ssp_tokens.hash_token(tok)
    assert ssp_tokens.verify_token(tok, h)
    assert not ssp_tokens.verify_token(tok + "x", h)
    assert not ssp_tokens.verify_token(None, h)
    assert not ssp_tokens.verify_token("", h)


def test_missing_hash_is_fail_closed():
    """S-06: пустой хэш = токен не выдан/отозван -> партнёр не авторизован."""
    assert not ssp_tokens.verify_token(None, None)
    assert not ssp_tokens.verify_token("anything", "")
    assert not ssp_tokens.verify_token("anything", None)


def test_previous_token_accepted_only_inside_rotation_window():
    old, new = ssp_tokens.generate_token(), ssp_tokens.generate_token()
    h_old, h_new = ssp_tokens.hash_token(old), ssp_tokens.hash_token(new)
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    until = now + timedelta(minutes=5)

    assert ssp_tokens.verify_token(new, h_new, h_old, until, now=now)
    assert ssp_tokens.verify_token(old, h_new, h_old, until, now=now)
    # Окно закрыто — старый токен больше не принимается.
    assert not ssp_tokens.verify_token(old, h_new, h_old, until, now=until)
    assert not ssp_tokens.verify_token(old, h_new, h_old, until, now=until + timedelta(seconds=1))
    # Без срока — prev не учитывается вовсе.
    assert not ssp_tokens.verify_token(old, h_new, h_old, None, now=now)
    # Naive datetime трактуется как UTC.
    assert ssp_tokens.verify_token(old, h_new, h_old, until.replace(tzinfo=None), now=now)


def test_validate_ip_whitelist_normalizes_and_rejects_garbage():
    ok = ssp_tokens.validate_ip_whitelist([" 203.0.113.7 ", "10.1.2.3/16", "2001:db8::1/32", ""])
    assert ok == ["203.0.113.7", "10.1.0.0/16", "2001:db8::/32"]
    with pytest.raises(ValueError, match="garbage"):
        ssp_tokens.validate_ip_whitelist(["203.0.113.7", "garbage"])
    with pytest.raises(ValueError, match="999.1.1.1"):
        ssp_tokens.validate_ip_whitelist(["999.1.1.1"])
    assert ssp_tokens.validate_ip_whitelist([]) == []
