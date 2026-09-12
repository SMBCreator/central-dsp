"""Decimal money helpers.

All money amounts inside the platform are `Decimal` quantized to 6 decimal
places (matches NUMERIC(14,6) in Postgres). Floats are accepted only at the
system boundary (OpenRTB JSON, query params) and converted immediately.

Currency is RUB system-wide; bid prices are CPM (per 1000 impressions).
"""
import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CURRENCY = os.environ.get("RTB_CURRENCY", "RUB")

_Q6 = Decimal("0.000001")
_THOUSAND = Decimal(1000)

ZERO = Decimal(0)


def money(value) -> Decimal:
    """Convert any numeric input to a 6-dp Decimal. Raises ValueError on junk."""
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            # Convert floats via str() to avoid binary-float artifacts
            # (Decimal(0.1) == 0.1000000000000000055511151231257827)
            d = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError(f"not a money value: {value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"non-finite money value: {value!r}")
    return d.quantize(_Q6, rounding=ROUND_HALF_UP)


def parse_money(value, default: Decimal = ZERO) -> Decimal:
    """Lenient boundary parser: junk/None -> default (never raises)."""
    if value is None:
        return default
    try:
        return money(value)
    except ValueError:
        return default


def cpm_to_cost(cpm_price) -> Decimal:
    """CPM price -> cost of a single impression."""
    return (money(cpm_price) / _THOUSAND).quantize(_Q6, rounding=ROUND_HALF_UP)


def clamp_clearing_price(reported, bid_price) -> Decimal:
    """Second-price sanity: 0 <= clearing price <= our bid price.

    SSPs substitute ${AUCTION_PRICE} into the win notice; a malicious or buggy
    caller can send anything. Never bill above what we actually bid; junk,
    negative or missing values fall back to the bid price (first-price safe
    default), zero is accepted as an explicit free win.
    """
    bid = money(bid_price)
    if reported is None:
        return bid
    try:
        reported_d = money(reported)
    except ValueError:
        return bid
    if reported_d < ZERO:
        return bid
    return min(reported_d, bid)
