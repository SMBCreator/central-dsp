"""ID sync (план predeploy A.8): валидация, подпись, allowlist, consent, TTL — без БД."""
from __future__ import annotations

import pytest

import id_sync
import startup_checks

# Тот же вектор проверяет bidder-go (internal/rtb/identity_test.go): формула
# хэша обязана совпадать байт в байт, иначе lookup в Redis никогда не попадёт.
HASH_VECTOR = ("salt-1", "ssp-user-42")


def _identity(**kw) -> id_sync.PartnerIdentity:
    base = {
        "sync_enabled": True,
        "sync_callback_url_template": "https://sync.ssp.example/match?dsp=centraldsp&uid={dsp_uid}",
        "sync_redirect_hosts": ["sync.ssp.example"],
    }
    base.update(kw)
    return id_sync.PartnerIdentity.from_json(id_sync.validate_identity_config(base))


def test_hash_vector_matches_go():
    assert id_sync.hash_ssp_uid(*HASH_VECTOR).hex() == "17d9964dcad2521174bf8e4c162febb0133cd95aa32795ec8d9ea81090f6c7f9"
    assert id_sync.redis_key("p1", b"\x01\xff") == "idsync:p1:01ff"


@pytest.mark.parametrize("uid,ok", [
    ("abc-123_XYZ.~:@=+/", True),
    ("a" * 128, True),
    ("a" * 129, False),           # oversized
    ("", False),
    (None, False),
    ("0000-0000", False),         # обнулённый ID
    ("abc\r\nSet-Cookie: x=1", False),  # CRLF-инъекция
    ("a b", False),
    ("x';DROP TABLE users;--", False),  # SQL-символы
    ("<script>", False),
    ("a%0d%0ab", False),
])
def test_valid_ssp_uid(uid, ok):
    assert id_sync.valid_ssp_uid(uid) is ok


def test_dsp_uid_is_random_128bit():
    a, b = id_sync.new_dsp_uid(), id_sync.new_dsp_uid()
    assert a != b and len(a) == 32 and id_sync.valid_dsp_uid(a)
    assert not id_sync.valid_dsp_uid("optout")
    assert not id_sync.valid_dsp_uid("A" * 32)


class TestConfigValidation:
    def test_defaults(self):
        cfg = id_sync.validate_identity_config({})
        assert cfg == {
            "sync_enabled": False, "sync_in_param": "ssp_uid", "sync_callback_url_template": None,
            "sync_redirect_hosts": [], "id_source": "user_id", "ttl_days": 90, "requires_consent": True,
        }

    @pytest.mark.parametrize("cfg,needle", [
        ({"sync_callback_url_template": "https://evil.example/{dsp_uid}", "sync_redirect_hosts": ["ssp.example"]}, "не входит"),
        ({"sync_callback_url_template": "http://ssp.example/{dsp_uid}", "sync_redirect_hosts": ["ssp.example"]}, "https"),
        ({"sync_callback_url_template": "https://ssp.example/x", "sync_redirect_hosts": ["ssp.example"]}, "{dsp_uid}"),
        ({"sync_callback_url_template": "https://u:p@ssp.example/{dsp_uid}", "sync_redirect_hosts": ["ssp.example"]}, "https"),
        ({"sync_redirect_hosts": ["https://ssp.example/path"]}, "доменное"),
        ({"sync_in_param": "bad param"}, "sync_in_param"),
        ({"id_source": "cookie"}, "id_source"),
        ({"ttl_days": 0}, "ttl_days"),
        ({"ttl_days": 1000}, "ttl_days"),
        ({"sync_enabled": True, "id_source": "buyeruid"}, "sync_enabled"),
    ])
    def test_rejects(self, cfg, needle):
        with pytest.raises(ValueError) as exc:
            id_sync.validate_identity_config(cfg)
        assert needle in str(exc.value)

    def test_eids_source_and_host_normalisation(self):
        cfg = id_sync.validate_identity_config({"id_source": "eids:ssp.example", "sync_redirect_hosts": [" SSP.Example. "]})
        assert cfg["id_source"] == "eids:ssp.example" and cfg["sync_redirect_hosts"] == ["ssp.example"]


class TestRedirect:
    def test_builds_url_with_uid(self):
        uid = id_sync.new_dsp_uid()
        assert id_sync.build_redirect(_identity(), uid) == f"https://sync.ssp.example/match?dsp=centraldsp&uid={uid}"

    def test_open_redirect_blocked_even_if_json_tampered(self):
        ident = _identity()
        ident.sync_callback_url_template = "https://evil.example/?u={dsp_uid}"
        assert id_sync.build_redirect(ident, id_sync.new_dsp_uid()) is None
        ident.sync_callback_url_template = "//evil.example/{dsp_uid}"
        assert id_sync.build_redirect(ident, id_sync.new_dsp_uid()) is None

    def test_rejects_non_uid(self):
        assert id_sync.build_redirect(_identity(), "optout") is None


class TestConsent:
    def test_requires_consent_by_default(self):
        ident = _identity()
        assert id_sync.consent_scope(ident, None, None, None) is None
        assert id_sync.consent_scope(ident, "1", "CPXxyz", None) == "gdpr"
        assert id_sync.consent_scope(ident, None, "agree-v1", None) == "consent"

    def test_partner_without_consent_requirement(self):
        ident = _identity(requires_consent=False)
        assert id_sync.consent_scope(ident, None, None, None) == "none"

    @pytest.mark.parametrize("gdpr,consent,usp", [
        ("1", "", None),          # gdpr применим, согласия нет
        (None, None, "1YYN"),     # отказ от продажи
        (None, "x" * 3000, None),  # мусорная строка согласия
    ])
    def test_always_denied(self, gdpr, consent, usp):
        assert id_sync.consent_scope(_identity(requires_consent=False), gdpr, consent, usp) is None


class TestSignature:
    def test_roundtrip_and_ttl(self, monkeypatch):
        monkeypatch.setenv("ID_SYNC_SECRET", "s" * 40)
        p = id_sync.signed_out_params("pk", now=1_000_000)
        assert id_sync.verify_signature("pk", p["ts"], p["sig"], now=1_000_000 + 599)
        assert not id_sync.verify_signature("pk", p["ts"], p["sig"], now=1_000_000 + 601)  # TTL 10 мин
        assert not id_sync.verify_signature("other", p["ts"], p["sig"], now=1_000_000)
        assert not id_sync.verify_signature("pk", str(int(p["ts"]) + 1), p["sig"], now=1_000_000)
        assert not id_sync.verify_signature("pk", p["ts"], "0" * 64, now=1_000_000)
        assert not id_sync.verify_signature("pk", None, None)
        assert not id_sync.verify_signature("pk", "1e9", p["sig"])

    def test_no_secret_no_signature(self, monkeypatch):
        monkeypatch.delenv("ID_SYNC_SECRET", raising=False)
        with pytest.raises(RuntimeError):
            id_sync.signed_out_params("pk")
        assert not id_sync.verify_signature("pk", "1", "a" * 64, now=1)


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("ID_SYNC_ENABLED", raising=False)
    assert id_sync.enabled() is False
    monkeypatch.setenv("ID_SYNC_ENABLED", "1")
    assert id_sync.enabled() is True


def test_startup_requires_secret_only_when_enabled():
    base = {"EVENT_TOKEN_SECRET": "e" * 40, "CRON_SECRET": "c" * 40, "RTB_API_TOKEN": "r" * 40,
            "DATABASE_URL": "postgresql://dsp:Strong-Pass-123@db:5432/dsp", "REGISTRATION_MODE": "closed"}
    assert not any("ID_SYNC_SECRET" in p for p in startup_checks.collect_problems(base))
    on = {**base, "ID_SYNC_ENABLED": "1"}
    assert any("ID_SYNC_SECRET" in p for p in startup_checks.collect_problems(on))
    assert not any("ID_SYNC_SECRET" in p for p in startup_checks.collect_problems({**on, "ID_SYNC_SECRET": "z" * 40}))
