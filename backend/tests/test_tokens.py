"""Unit tests for HMAC-signed event tokens (tokens.py)."""
import os
import time

import pytest

import tokens
from tokens import sign_event_token, verify_event_token


def test_sign_then_verify_roundtrip():
    tok = sign_event_token("bid-abc-123")
    assert verify_event_token(tok) == "bid-abc-123"


def test_token_format():
    tok = sign_event_token("b1")
    version, bid_id, exp, sig = tok.split(".")
    assert version == "v1"
    assert bid_id == "b1"
    assert int(exp) > time.time()
    assert len(sig) == 32


def test_tampered_bid_id_rejected():
    tok = sign_event_token("victim-bid")
    _, _, exp, sig = tok.split(".")
    forged = f"v1.other-bid.{exp}.{sig}"
    assert verify_event_token(forged) is None


def test_tampered_expiry_rejected():
    tok = sign_event_token("b1")
    v, bid_id, exp, sig = tok.split(".")
    forged = f"{v}.{bid_id}.{int(exp) + 99999}.{sig}"
    assert verify_event_token(forged) is None


def test_expired_token_rejected():
    tok = sign_event_token("b1", ttl=-10)
    assert verify_event_token(tok) is None


def test_garbage_rejected_when_unsigned_disallowed(monkeypatch):
    monkeypatch.delenv("RTB_ALLOW_UNSIGNED_EVENTS", raising=False)
    assert verify_event_token("just-a-bare-bid-id") is None
    assert verify_event_token("") is None
    assert verify_event_token("v1.only.three") is None


def test_legacy_bare_bid_id_allowed_during_rollout(monkeypatch):
    monkeypatch.setenv("RTB_ALLOW_UNSIGNED_EVENTS", "1")
    assert verify_event_token("legacy-bid-id") == "legacy-bid-id"


def test_secret_change_invalidates_tokens(monkeypatch):
    tok = sign_event_token("b1")
    monkeypatch.setenv("EVENT_TOKEN_SECRET", "a-completely-different-secret")
    assert verify_event_token(tok) is None


def test_prod_refuses_dev_fallback_secret(monkeypatch):
    """GAP-003: with APP_ENV=production and no secret configured, signing
    must fail instead of silently using the public dev fallback."""
    import tokens as tokens_mod

    monkeypatch.delenv("EVENT_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        tokens_mod.sign_event_token("b1")


def test_dev_fallback_still_works_without_app_env(monkeypatch):
    monkeypatch.delenv("EVENT_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    tok = sign_event_token("b1")
    assert verify_event_token(tok) == "b1"
