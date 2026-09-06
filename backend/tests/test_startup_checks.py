"""Чеклист 6.1 «Проверка при старте» / 6.11 «CRON_SECRET пустой»:
в production процесс не поднимается с пустыми, плейсхолдерными или короткими
секретами; вне production — только предупреждения."""

import pytest

import startup_checks as sc

GOOD = "a" * 64
PROD_OK = {
    "APP_ENV": "production",
    "EVENT_TOKEN_SECRET": GOOD,
    "CRON_SECRET": "b" * 40,
    "RTB_API_TOKEN": "c" * 32,
    "DATABASE_URL": "postgresql://dsp:" + "d" * 24 + "@postgres:5432/dsp",
}


def test_good_production_config_passes():
    assert sc.collect_problems(PROD_OK) == []
    sc.assert_production_config(PROD_OK)  # не бросает


@pytest.mark.parametrize(
    "name",
    ["EVENT_TOKEN_SECRET", "CRON_SECRET", "RTB_API_TOKEN"],
)
def test_missing_secret_is_fatal_in_production(name):
    env = dict(PROD_OK)
    del env[name]
    with pytest.raises(RuntimeError) as ei:
        sc.assert_production_config(env)
    assert name in str(ei.value)


@pytest.mark.parametrize(
    "value, fragment",
    [
        ("", "не задан"),
        ("   ", "не задан"),
        ("CHANGE_ME_openssl_rand_hex_32", "плейсхолдер"),
        ("change-me", "плейсхолдер"),
        ("dev", "слабое"),
        ("secret", "слабое"),
        ("short-but-random-1234", "короче"),
    ],
)
def test_bad_cron_secret_values(value, fragment):
    env = dict(PROD_OK, CRON_SECRET=value)
    problems = sc.collect_problems(env)
    assert any("CRON_SECRET" in p and fragment in p for p in problems), problems


def test_session_secret_is_accepted_as_event_secret_fallback():
    env = dict(PROD_OK)
    del env["EVENT_TOKEN_SECRET"]
    env["SESSION_SECRET"] = GOOD
    assert sc.collect_problems(env) == []


def test_default_postgres_password_is_fatal():
    env = dict(PROD_OK, DATABASE_URL="postgresql://postgres:postgres@postgres:5432/dsp")
    problems = sc.collect_problems(env)
    assert any("DATABASE_URL" in p for p in problems), problems
    with pytest.raises(RuntimeError):
        sc.assert_production_config(env)


def test_database_url_without_password_is_not_our_problem():
    env = dict(PROD_OK, DATABASE_URL="postgresql://dsp@localhost:5433/dsp_ci")
    assert sc.collect_problems(env) == []


@pytest.mark.parametrize("app_env", ["", "dev", "development", "staging-ish", "test"])
def test_outside_production_problems_only_warn(app_env, caplog):
    env = {"APP_ENV": app_env, "CRON_SECRET": "", "DATABASE_URL": "postgresql://postgres:postgres@x/db"}
    with caplog.at_level("WARNING", logger="startup_checks"):
        sc.assert_production_config(env)  # не бросает
    assert any("CRON_SECRET" in r.getMessage() for r in caplog.records)


def test_warnings_are_not_fatal_but_logged(caplog):
    env = dict(PROD_OK, REGISTRATION_MODE="open", ALLOW_MOCK_FALLBACK="1")
    with caplog.at_level("WARNING", logger="startup_checks"):
        sc.assert_production_config(env)
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "REGISTRATION_MODE" in msgs
    assert "ALLOW_MOCK_FALLBACK" in msgs


# --- S-04: dev-лазейки в production фатальны ----------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
def test_python_serving_enabled_is_fatal_in_production(value):
    env = dict(PROD_OK, RTB_PYTHON_SERVING=value)
    with pytest.raises(RuntimeError, match="RTB_PYTHON_SERVING"):
        sc.assert_production_config(env)


def test_python_serving_off_or_unset_is_fine():
    assert sc.collect_problems(dict(PROD_OK, RTB_PYTHON_SERVING="false")) == []
    assert sc.collect_problems(PROD_OK) == []


def test_unsigned_events_is_fatal_in_production():
    env = dict(PROD_OK, RTB_ALLOW_UNSIGNED_EVENTS="1")
    with pytest.raises(RuntimeError, match="RTB_ALLOW_UNSIGNED_EVENTS"):
        sc.assert_production_config(env)


def test_unknown_registration_mode_is_fatal_in_production():
    env = dict(PROD_OK, REGISTRATION_MODE="Opened")
    with pytest.raises(RuntimeError, match="REGISTRATION_MODE"):
        sc.assert_production_config(env)


@pytest.mark.parametrize(
    "raw,expected",
    [(None, "closed"), ("", "closed"), ("open", "open"), ("OPEN ", "open"), ("closed", "closed"),
     ("bogus", "closed"), ("Closed", "closed")],
)
def test_registration_mode_defaults_closed_and_fails_closed(raw, expected):
    env = {} if raw is None else {"REGISTRATION_MODE": raw}
    assert sc.registration_mode(env) == expected


def test_unknown_registration_mode_outside_production_is_warning_only(caplog):
    env = {"APP_ENV": "dev", "REGISTRATION_MODE": "bogus"}
    with caplog.at_level("WARNING", logger="startup_checks"):
        sc.assert_production_config(env)
    assert any("REGISTRATION_MODE" in r.getMessage() for r in caplog.records)
    assert sc.registration_mode(env) == "closed"


def test_prod_detection_variants():
    assert sc.is_production({"APP_ENV": "production"})
    assert sc.is_production({"APP_ENV": " Prod "})
    assert not sc.is_production({"APP_ENV": "dev"})
    assert not sc.is_production({})
