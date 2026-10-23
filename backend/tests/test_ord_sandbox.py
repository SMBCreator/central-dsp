"""ord/sandbox.py: сквозной прогон песочницы на MockTransport (без сети)."""
import asyncio
import json
import random
from datetime import date

import httpx
import pytest

from ord import sandbox
from ord.sandbox import STEP_KEYS, SandboxRunner, build_context, inn10_checksum_ok, random_inn10

KEY = "sbx-secret-key-1234567890"


def _ok_handler(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.url.host == "api-ord-sandbox.ozon.ru"
        assert req.headers["authorization"] == f"Bearer {KEY}"
        path = req.url.path
        if path == "/api/external/dict/kktu":
            assert req.headers["accept"] == "application/json"
            return httpx.Response(200, json=[
                {"code": "30.15.1", "name": "Лист", "level": 3, "parentCode": "30.15", "isUsableCategory": True},
                {"code": "99.1.1", "name": "Удалён", "level": 3, "deletedAt": "2025-01-01 00:00:00"},
            ])
        if path == "/api/external/v4/creative":
            return httpx.Response(200, json={"creative": {"marker": "2SDnjerid"}})
        if path == "/api/external/notification/error_list":
            return httpx.Response(200, json={"notification": []})
        return httpx.Response(200, json={})
    return handler


def _run(handler, ctx=None, only=None):
    ctx = ctx or build_context({"run_id": "t1"}, today=date(2026, 10, 23))
    runner = SandboxRunner(KEY, transport=httpx.MockTransport(handler))
    return asyncio.run(runner.run(ctx, only))


def test_inn_checksum():
    assert inn10_checksum_ok("7707083893")
    assert not inn10_checksum_ok("7707083894")
    assert not inn10_checksum_ok("77070838")
    rng = random.Random(1)
    assert all(inn10_checksum_ok(random_inn10(rng)) for _ in range(200))


def test_context_ids_unique_per_run_and_previous_month():
    a = build_context({"run_id": "aaa"}, today=date(2026, 1, 15))
    b = build_context({"run_id": "bbb"}, today=date(2026, 1, 15))
    assert a["period"] == "2025-12" and a["period_start"] == "2025-12-01" and a["period_end"] == "2025-12-31"
    assert a["contract"]["external_id"] != b["contract"]["external_id"]
    assert a["creative"]["external_creative_id"].startswith("sbx-aaa-")


def test_full_run_all_ok_uses_production_mapping():
    calls = []
    steps = _run(_ok_handler(calls))
    assert [s["key"] for s in steps] == STEP_KEYS
    assert all(s["status"] == "ok" for s in steps), steps
    by = {s["key"]: s for s in steps}
    assert by["creative"]["erid"] == "2SDnjerid"
    assert by["kktu"]["kktu"][1]["deleted"] is True
    creative = json.loads(next(c for c in calls if c.url.path == "/api/external/v4/creative").content)
    assert creative["kktuCategory"] == ["30.15.1"]
    assert creative["externalContractIds"] == ["sbx-t1-contract"]
    contract = by["contract"]["request"]["body"]
    assert contract["externalOrganisationCustomerId"] == "sbx-t1-org-rd"
    assert contract["externalOrganisationPerformerId"] == "sbx-t1-org-ors"
    stat = by["statistic"]["request"]["body"]["statistics"][0]
    assert stat["externalPlatformId"] == "sbx-t1-platform"
    assert stat["price"]["vatRate"] == "22"


def test_failure_skips_dependents_but_runs_independent():
    def handler(req):
        if req.url.path == "/api/external/v6/contract":
            return httpx.Response(400, json={"message": "bad contractDate"})
        return _ok_handler([])(req)

    by = {s["key"]: s for s in _run(handler)}
    assert by["contract"]["status"] == "error" and "HTTP 400" in by["contract"]["error"]
    assert "bad contractDate" in by["contract"]["response"]
    for k in ("creative", "statistic", "invoice", "invoice_link"):
        assert by[k]["status"] == "skipped", k
    for k in ("kktu", "platform", "notifications"):
        assert by[k]["status"] == "ok", k


def test_bad_key_hint_and_key_not_leaked():
    def handler(req):
        return httpx.Response(401, text=f"invalid token {KEY}")

    steps = _run(handler)
    assert steps[0]["status"] == "error" and "песочницы" in steps[0]["error"]
    assert all(s["status"] == "skipped" for s in steps[1:])
    assert KEY not in json.dumps(steps, ensure_ascii=False)


def test_creative_without_erid_is_error():
    def handler(req):
        if req.url.path == "/api/external/v4/creative":
            return httpx.Response(200, json={"creative": {}})
        return _ok_handler([])(req)

    by = {s["key"]: s for s in _run(handler)}
    assert by["creative"]["status"] == "error" and "erid" in by["creative"]["error"]
    assert by["invoice_link"]["status"] == "skipped"


def test_network_error_reported():
    def handler(req):
        raise httpx.ConnectError("boom")

    steps = _run(handler, only=["ping"])
    assert steps == [steps[0]] and steps[0]["status"] == "error" and "Сеть" in steps[0]["error"]


def test_only_subset_skips_unselected_dependencies():
    steps = _run(_ok_handler([]), only=["ping", "creative"])
    assert [s["key"] for s in steps] == ["ping", "creative"]
    assert steps[1]["status"] == "skipped"


def test_host_is_hardcoded_sandbox():
    assert sandbox.SANDBOX_BASE_URL == "https://api-ord-sandbox.ozon.ru"


@pytest.mark.parametrize("inn", ["7707083894", "123"])
def test_endpoint_rejects_bad_inn(inn):
    from pydantic import ValidationError
    from routers.settings import OrdSandboxRunIn

    if len(inn) != 10:
        with pytest.raises(ValidationError):
            OrdSandboxRunIn(api_key=KEY, advertiser_inn=inn)
    else:
        assert not inn10_checksum_ok(OrdSandboxRunIn(api_key=KEY, advertiser_inn=inn).advertiser_inn)
