"""Audit R-01: SSP получают публичный URL Go-биддера, а не /api/rtb/bid backend'а.

Источник истины — PUBLIC_BIDDER_BASE_URL (fallback PUBLIC_BASE_URL при
единственном домене за Caddy). Без переменных UI получает None и показывает
предупреждение вместо того, чтобы «угадать» origin кабинета — именно так
партнёру раньше уходил адрес отключённого Python-биддера.
"""

import startup_checks as sc
from routers.settings import public_bidder_base_url


def test_explicit_bidder_url_wins_over_public_base():
    env = {"PUBLIC_BIDDER_BASE_URL": "https://rtb.example.com/", "PUBLIC_BASE_URL": "https://dsp.example.com"}
    assert public_bidder_base_url(env) == "https://rtb.example.com"


def test_falls_back_to_public_base_url_single_domain_topology():
    assert public_bidder_base_url({"PUBLIC_BASE_URL": "https://dsp.example.com/"}) == "https://dsp.example.com"


def test_blank_values_mean_not_configured():
    assert public_bidder_base_url({"PUBLIC_BIDDER_BASE_URL": "   ", "PUBLIC_BASE_URL": ""}) is None
    assert public_bidder_base_url({}) is None


def test_missing_public_url_is_a_startup_warning():
    env = {"APP_ENV": "production"}
    joined = "\n".join(sc.collect_warnings(env))
    assert "PUBLIC_BIDDER_BASE_URL" in joined and "R-01" in joined


def test_configured_public_url_is_not_a_warning():
    env = {"APP_ENV": "production", "PUBLIC_BIDDER_BASE_URL": "https://rtb.example.com"}
    assert not any("PUBLIC_BIDDER_BASE_URL" in w for w in sc.collect_warnings(env))
