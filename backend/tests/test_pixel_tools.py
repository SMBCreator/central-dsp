"""Тесты Части 2: пиксель (скрипт, парсинг UA, scroll clamp) и шаблоны правил."""
import pytest
from pydantic import ValidationError

from routers.pixel import _SCRIPT_TEMPLATE, _parse_device, GoalIn, PixelIn
from routers.rules import RULE_TEMPLATES
from schemas import RuleIn


class TestTrackingScript:
    def test_script_measures_scroll(self):
        assert "maxScroll" in _SCRIPT_TEMPLATE
        assert '"scroll"' in _SCRIPT_TEMPLATE
        assert "sp: maxScroll" in _SCRIPT_TEMPLATE

    def test_script_keeps_core_features(self):
        # Ключевые механизмы не потеряны при добавлении скролла
        assert "cdsp_clid" in _SCRIPT_TEMPLATE          # атрибуция клика
        assert "sendBeacon" in _SCRIPT_TEMPLATE          # неблокирующая отправка
        assert "heartbeat" in _SCRIPT_TEMPLATE           # время на сайте
        assert "cdsp = { track:" in _SCRIPT_TEMPLATE     # событийные цели
        assert "pagehide" in _SCRIPT_TEMPLATE            # финальный маячок

    def test_script_placeholders(self):
        assert "__PIXEL_ID__" in _SCRIPT_TEMPLATE
        assert "__API_BASE__" in _SCRIPT_TEMPLATE


class TestParseDevice:
    def test_desktop_chrome_windows(self):
        d, b, o = _parse_device("Mozilla/5.0 (Windows NT 10.0) Chrome/120.0", 1920)
        assert (d, b, o) == ("desktop", "chrome", "windows")

    def test_android_phone(self):
        d, _, o = _parse_device("Mozilla/5.0 (Linux; Android 14; Mobile) Chrome/120", 400)
        assert d == "phone"
        assert o == "android"

    def test_empty_ua_is_desktop_other(self):
        d, b, o = _parse_device("", 0)
        assert (d, b, o) == ("desktop", "other", "other")


class TestScrollClamp:
    """Клэмп scroll_pct повторяет логику collect: 0..100, мусор -> 0."""

    @staticmethod
    def _clamp(raw):
        try:
            return min(max(int(raw or 0), 0), 100)
        except (ValueError, TypeError):
            return 0

    def test_valid_values(self):
        assert self._clamp(55) == 55
        assert self._clamp(0) == 0
        assert self._clamp(100) == 100

    def test_out_of_range(self):
        assert self._clamp(150) == 100
        assert self._clamp(-5) == 0

    def test_garbage(self):
        assert self._clamp("abc") == 0
        assert self._clamp(None) == 0


class TestPixelSchemas:
    def test_goal_types(self):
        for gt in ("event", "url", "duration"):
            g = GoalIn(name="g", goal_type=gt, match_value="x")
            assert g.goal_type == gt

    def test_invalid_goal_type(self):
        with pytest.raises(ValidationError):
            GoalIn(name="g", goal_type="scroll", match_value="x")

    def test_pixel_domain_required(self):
        with pytest.raises(ValidationError):
            PixelIn(name="p", domain="ab")


class TestRuleTemplates:
    def test_templates_not_empty(self):
        assert len(RULE_TEMPLATES) >= 6

    def test_unique_keys(self):
        keys = [t["key"] for t in RULE_TEMPLATES]
        assert len(keys) == len(set(keys))

    def test_every_template_is_valid_rule(self):
        """Каждый шаблон должен без изменений проходить валидацию RuleIn."""
        for t in RULE_TEMPLATES:
            rule = RuleIn(
                name=t["name"],
                metric=t["metric"],
                operator=t["operator"],
                threshold=t["threshold"],
                action=t["action"],
                action_value=t["action_value"],
                window_hours=t["window_hours"],
            )
            assert rule.name == t["name"]

    def test_templates_have_descriptions(self):
        for t in RULE_TEMPLATES:
            assert t["description"], f"нет описания у {t['key']}"
