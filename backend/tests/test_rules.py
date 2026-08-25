"""Tests for the expanded auto-rules engine (Phase C)."""
import pytest
from pydantic import ValidationError

from schemas import RuleIn
from rules_engine import _condition_met


class TestRuleSchema:
    def test_new_metrics_accepted(self):
        for metric in ("cpa", "cvr", "conversions", "budget_spent_pct", "cpc"):
            rule = RuleIn(name="r", metric=metric, operator="gt", threshold=1.0, action="notify")
            assert rule.metric == metric

    def test_new_actions_accepted(self):
        for action in ("decrease_daily_budget", "increase_daily_budget", "blacklist_domains", "pause_line_item"):
            rule = RuleIn(name="r", metric="ctr", operator="lt", threshold=0.1, action=action)
            assert rule.action == action

    def test_invalid_metric_rejected(self):
        with pytest.raises(ValidationError):
            RuleIn(name="r", metric="roi", operator="gt", threshold=1.0, action="notify")

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            RuleIn(name="r", metric="ctr", operator="gt", threshold=1.0, action="delete_everything")

    def test_all_operators(self):
        for op in ("gt", "lt", "gte", "lte"):
            rule = RuleIn(name="r", metric="spend", operator=op, threshold=100, action="notify")
            assert rule.operator == op

    def test_window_bounds(self):
        with pytest.raises(ValidationError):
            RuleIn(name="r", metric="ctr", operator="gt", threshold=1, action="notify", window_hours=0)
        with pytest.raises(ValidationError):
            RuleIn(name="r", metric="ctr", operator="gt", threshold=1, action="notify", window_hours=200)


class TestConditionMet:
    def test_gt(self):
        assert _condition_met(5.0, "gt", 4.0) is True
        assert _condition_met(4.0, "gt", 4.0) is False

    def test_lt(self):
        assert _condition_met(3.0, "lt", 4.0) is True
        assert _condition_met(4.0, "lt", 4.0) is False

    def test_gte_lte(self):
        assert _condition_met(4.0, "gte", 4.0) is True
        assert _condition_met(4.0, "lte", 4.0) is True
        assert _condition_met(3.9, "gte", 4.0) is False
