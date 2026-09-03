"""Хэширование и сверка токенов SSP-партнёров (backend/ssp_tokens.py, миграция 032)."""

import hashlib

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
    # Токен партнёру не выдан — проверка не требуется.
    assert ssp_tokens.verify_token(None, None)
    assert ssp_tokens.verify_token("anything", "")
