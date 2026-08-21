"""Unit tests for the Decimal money helpers (no DB required)."""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from money import clamp_clearing_price, cpm_to_cost, money, parse_money, ZERO


class TestMoney:
    def test_float_artifacts_are_gone(self):
        assert money(0.1) == Decimal("0.1")
        assert money(0.1) + money(0.2) == Decimal("0.3")

    def test_quantized_to_6dp(self):
        assert money("1.23456789") == Decimal("1.234568")

    def test_rejects_junk(self):
        for bad in ("abc", float("nan"), float("inf"), None, [], {}):
            with pytest.raises(ValueError):
                money(bad)

    def test_parse_money_lenient(self):
        assert parse_money("junk") == ZERO
        assert parse_money(None, default=Decimal(5)) == Decimal(5)
        assert parse_money("2.5") == Decimal("2.5")


class TestCpmToCost:
    def test_basic(self):
        assert cpm_to_cost(250) == Decimal("0.25")

    def test_sub_kopeck_precision(self):
        # 0.35 RUB CPM -> 0.00035 per impression, exact (would drift in float)
        assert cpm_to_cost("0.35") == Decimal("0.00035")

    def test_thousand_impressions_sum_exactly(self):
        cost = cpm_to_cost("2.37")
        assert sum([cost] * 1000, ZERO) == Decimal("2.37")


class TestClampClearingPrice:
    BID = Decimal("3.5")

    def test_second_price_below_bid_kept(self):
        assert clamp_clearing_price("2.1", self.BID) == Decimal("2.1")

    def test_above_bid_clamped(self):
        # SSP cannot make us pay more than we bid
        assert clamp_clearing_price("999999", self.BID) == self.BID

    def test_missing_falls_back_to_bid(self):
        assert clamp_clearing_price(None, self.BID) == self.BID

    def test_junk_falls_back_to_bid(self):
        assert clamp_clearing_price("${AUCTION_PRICE}", self.BID) == self.BID
        assert clamp_clearing_price("nan", self.BID) == self.BID

    def test_negative_falls_back_to_bid(self):
        assert clamp_clearing_price("-5", self.BID) == self.BID

    def test_zero_is_accepted(self):
        assert clamp_clearing_price("0", self.BID) == ZERO
