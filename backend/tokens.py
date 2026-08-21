"""HMAC-signed event tokens for win/imp/click/vevent URLs.

Every bid response embeds tracking URLs. Previously they carried a bare
bid_id, so anyone who saw (or guessed) a bid_id could fire billable events.
Now the `bid` query parameter is a signed token:

    v1.<bid_id>.<exp_unix>.<sig>

where sig = hex(HMAC-SHA256(secret, "v1|bid_id|exp"))[:32].

Verification is stateless (no DB/Redis hit) and constant-time. Expiry limits
the replay window; billing idempotency (DB unique index + mark_once) already
prevents duplicate charges, so exp is a second line of defense, not the first.

Env:
  EVENT_TOKEN_SECRET        - required in production (any long random string)
  RTB_ALLOW_UNSIGNED_EVENTS - "1" accepts legacy bare bid_ids during rollout
"""
import hashlib
import hmac
import os
import time

_VERSION = "v1"
DEFAULT_TTL_SECONDS = 6 * 3600  # tracking pixels can fire long after the auction


def _secret() -> bytes:
    s = os.environ.get("EVENT_TOKEN_SECRET") or os.environ.get("SESSION_SECRET")
    if not s:
        # Deterministic dev fallback so local runs work out of the box.
        # The production deploy checklist requires EVENT_TOKEN_SECRET.
        s = "centraldsp-dev-event-secret"
    return s.encode()


def _sig(bid_id: str, exp: int) -> str:
    msg = f"{_VERSION}|{bid_id}|{exp}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]


def sign_event_token(bid_id: str, ttl: int = DEFAULT_TTL_SECONDS) -> str:
    exp = int(time.time()) + ttl
    return f"{_VERSION}.{bid_id}.{exp}.{_sig(bid_id, exp)}"


def verify_event_token(token: str) -> str | None:
    """Return the embedded bid_id when the token is valid, else None.

    Falls back to treating `token` as a legacy bare bid_id only when
    RTB_ALLOW_UNSIGNED_EVENTS=1 (rollout escape hatch).
    """
    if token.startswith(_VERSION + "."):
        parts = token.split(".")
        if len(parts) != 4:
            return None
        _, bid_id, exp_s, sig = parts
        try:
            exp = int(exp_s)
        except ValueError:
            return None
        if exp < int(time.time()):
            return None
        if not hmac.compare_digest(sig, _sig(bid_id, exp)):
            return None
        return bid_id
    if os.environ.get("RTB_ALLOW_UNSIGNED_EVENTS") == "1":
        return token or None
    return None
