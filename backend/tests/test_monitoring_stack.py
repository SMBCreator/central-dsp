"""Контракт стека мониторинга (audit O-05): compose.yml <-> ops/prometheus/*.

Docker в unit-тестах недоступен, поэтому проверяем YAML статически:
  - каждый scrape-target Prometheus — существующий compose-сервис в сети
    centraldsp на объявленном порту;
  - node-exporter поднимается только profile monitoring, без публикации
    портов, все host-mount'ы read-only, без pid: host, capabilities сброшены;
  - HEALTHCHECK контейнеров = liveness (/health, `-healthcheck` -> /livez),
    readiness через них не ходит;
  - каждое правило из групп O-05 и все правила, которые до O-05 не могли
    сработать из-за label mismatch, покрыты promtool-тестом (alerts_test.yml),
    и тесты не ссылаются на несуществующие правила.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
PROM = yaml.safe_load((ROOT / "ops" / "prometheus" / "prometheus.yml").read_text(encoding="utf-8"))
ALERTS = yaml.safe_load((ROOT / "ops" / "prometheus" / "alerts.yml").read_text(encoding="utf-8"))
ALERT_TESTS = yaml.safe_load((ROOT / "ops" / "prometheus" / "alerts_test.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]

# Правила, которые до O-05 сравнивали серии разных скрейпов / label'ы и
# молча не срабатывали — регресс должен ловиться promtool-тестом.
LABEL_MISMATCH_RULES = {
    "CronRetentionStale",
    "BidderNoBidSpike",
    "BidderWinGapAnomaly",
    "FreqWinWithoutImpHigh",
    "RtbEventsConsumerSilent",
}
O05_GROUPS = {"centraldsp-availability", "centraldsp-node"}


def _rules_by_group() -> dict[str, list[str]]:
    return {g["name"]: [r["alert"] for r in g["rules"] if "alert" in r] for g in ALERTS["groups"]}


def _all_rule_names() -> set[str]:
    return {a for names in _rules_by_group().values() for a in names}


def _tested_alertnames() -> set[str]:
    names: set[str] = set()
    for t in ALERT_TESTS["tests"]:
        for case in t.get("promql_expr_test", []):
            names |= set(re.findall(r'ALERTS\{alertname="([A-Za-z0-9_]+)"', case["expr"]))
        for case in t.get("alert_rule_test", []):
            names.add(case["alertname"])
    return names


def _exposed_ports(service: dict) -> set[int]:
    ports: set[int] = set()
    for p in service.get("ports", []):
        target = p["target"] if isinstance(p, dict) else str(p).split(":")[-1].split("/")[0]
        ports.add(int(target))
    for e in service.get("expose", []):
        ports.add(int(str(e).split("/")[0]))
    return ports


def _scrape_targets() -> list[tuple[str, str, int]]:
    out = []
    for job in PROM["scrape_configs"]:
        for sc in job.get("static_configs", []):
            for t in sc["targets"]:
                host, port = t.rsplit(":", 1)
                out.append((job["job_name"], host, int(port)))
    return out


# ---------------------------------------------------------------- scrape targets

def test_every_scrape_target_is_a_compose_service_on_the_shared_network():
    for job, host, port in _scrape_targets():
        if host == "localhost":  # prometheus скрейпит сам себя
            assert job == "prometheus"
            continue
        assert host in SERVICES, f"scrape job {job!r}: сервиса {host!r} нет в compose.yml"
        assert "centraldsp" in SERVICES[host].get("networks", []), f"{host} не в сети centraldsp — Prometheus его не увидит"


@pytest.mark.parametrize("job,port", [("backend", 8000), ("scheduler", 8001), ("bidder-go", 8090), ("node", 9100)])
def test_expected_scrape_jobs_present(job, port):
    targets = {(j, p) for j, _h, p in _scrape_targets()}
    assert (job, port) in targets, f"нет scrape job {job} на порту {port}: {sorted(targets)}"


def test_scrape_job_names_match_alert_selectors():
    """TargetDown/NodeExporterDown фильтруют по job — имена должны совпадать со scrape_configs."""
    jobs = {j for j, _h, _p in _scrape_targets()}
    for g in ALERTS["groups"]:
        for r in g["rules"]:
            for m in re.finditer(r'(?<![A-Za-z_])job(=~?)"([^"]+)"', r["expr"]):
                wanted = set(m.group(2).split("|")) if m.group(1) == "=~" else {m.group(2)}
                assert wanted <= jobs, f"{r['alert']}: job {wanted - jobs} нет в prometheus.yml ({sorted(jobs)})"


# ---------------------------------------------------------------- node-exporter

def test_monitoring_services_only_under_profile():
    for svc in ("prometheus", "node-exporter", "alertmanager"):
        assert SERVICES[svc].get("profiles") == ["monitoring"], f"{svc} должен стартовать только с --profile monitoring"


def test_node_exporter_hardening():
    ne = SERVICES["node-exporter"]
    assert "ports" not in ne, "node-exporter не публикует 9100 — Prometheus ходит по внутренней сети"
    assert ne.get("pid") is None, "pid: host не нужен для filesystem/meminfo/loadavg — не расширять поверхность"
    assert ne.get("read_only") is True
    assert "ALL" in ne.get("cap_drop", [])
    assert "no-new-privileges:true" in ne.get("security_opt", [])
    for v in ne["volumes"]:
        src, dst, *opts = v.split(":")
        assert opts and "ro" in opts[0].split(","), f"host mount {src}->{dst} должен быть read-only"
    cmd = " ".join(ne["command"])
    for flag in ("--path.procfs=/host/proc", "--path.sysfs=/host/sys", "--path.rootfs=/rootfs"):
        assert flag in cmd
    assert "overlay" in cmd and "tmpfs" in cmd, "docker-слои и tmpfs должны быть исключены из filesystem-коллектора"


def test_node_exporter_mounts_match_path_flags():
    ne = SERVICES["node-exporter"]
    mounts = {v.split(":")[1] for v in ne["volumes"]}
    for flag in ne["command"]:
        m = re.match(r"--path\.(procfs|sysfs|rootfs)=(\S+)", flag)
        if m:
            assert m.group(2) in mounts, f"{flag}: путь не смонтирован ({sorted(mounts)})"


# ---------------------------------------------------------------- liveness vs readiness

def test_backend_and_scheduler_healthcheck_is_liveness_only():
    test = " ".join(SERVICES["backend"]["healthcheck"]["test"])
    assert "/health'" in test or "/health\"" in test or test.endswith("/health")
    assert "/ready" not in test, "Docker HEALTHCHECK = liveness; readiness читают preprod_check и Prometheus"


def test_bidder_healthcheck_uses_binary_flag_not_healthz_readiness():
    test = SERVICES["bidder-go"]["healthcheck"]["test"]
    assert test == ["CMD", "/bidder", "-healthcheck"]
    main_go = (ROOT / "bidder-go" / "cmd" / "bidder" / "main.go").read_text(encoding="utf-8")
    assert 'const livezPath = "/livez"' in main_go, "-healthcheck должен опрашивать /livez, не /healthz"


def test_preprod_check_gates_on_backend_ready():
    sh = (ROOT / "ops" / "preprod_check.sh").read_text(encoding="utf-8")
    assert '"$BACKEND_IN/ready"' in sh
    assert 'fail "backend /ready"' in sh


# ---------------------------------------------------------------- promtool unit-test coverage

def test_alert_tests_reference_existing_rules_only():
    unknown = _tested_alertnames() - _all_rule_names()
    assert not unknown, f"alerts_test.yml тестирует несуществующие правила: {sorted(unknown)}"


def test_alert_tests_use_rule_file_next_to_them():
    assert ALERT_TESTS["rule_files"] == ["alerts.yml"]


def test_label_mismatch_rules_and_o05_groups_are_unit_tested():
    by_group = _rules_by_group()
    for g in O05_GROUPS:
        assert g in by_group, f"группа {g} отсутствует в alerts.yml"
    required = set(LABEL_MISMATCH_RULES)
    for g in O05_GROUPS:
        required |= set(by_group[g])
    missing = required - _tested_alertnames()
    assert not missing, f"правила без promtool-теста в alerts_test.yml: {sorted(missing)}"


def test_cross_scrape_rules_aggregate_both_sides():
    """Серии bidder_* и rtb_*/cron_* приходят с разных job/instance: бинарный
    оператор между ними обязан идти через sum()/on(), иначе результат пуст."""
    for g in ALERTS["groups"]:
        for r in g["rules"]:
            expr = r["expr"]
            if re.search(r"\bbidder_[a-z_]+", expr) and re.search(r"\b(rtb|cron_job)_[a-z_]+", expr):
                assert "sum(" in expr or " on(" in expr or " on (" in expr, f"{r['alert']}: cross-job expr без sum()/on(): {expr}"


def test_no_alert_selects_scrape_job_label_for_exposition_job():
    """Метрика экспозиции с label job переименовывается Prometheus в exported_job.
    Селектор {job="<cron job>"} никогда не совпадёт — использовать cron_job."""
    cron_jobs = set(re.findall(r'cron_job="([a-z_]+)"', (ROOT / "ops" / "prometheus" / "alerts.yml").read_text(encoding="utf-8")))
    scrape_jobs = {j for j, _h, _p in _scrape_targets()}
    for g in ALERTS["groups"]:
        for r in g["rules"]:
            for m in re.finditer(r'(?<![A-Za-z_])job="([^"]+)"', r["expr"]):
                assert m.group(1) in scrape_jobs, f"{r['alert']}: job={m.group(1)!r} — это label экспозиции, а не scrape job ({sorted(cron_jobs)})"
