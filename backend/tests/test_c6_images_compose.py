"""C6 / O-07: контракт образов и compose — статически, без Docker.

* все внешние образы закреплены tag + sha256 digest (никаких плавающих тегов);
* PostgreSQL major одинаковый в compose и CI (15) — менять major в этой задаче нельзя;
* backend/scheduler стартуют через entrypoint (chown uploads -> setpriv 10001),
  healthcheck тоже не от root; frontend USER node; bidder distroless nonroot;
* backend ставит зависимости из requirements.lock с --require-hashes;
* soft reservations есть у всех сервисов, hard limits в compose.yml НЕТ
  (они только в compose.limits.yml, параметризованные без default);
* stop_grace_period backend >= uvicorn --timeout-graceful-shutdown.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class _ComposeLoader(yaml.SafeLoader):
    """compose.prod.yml использует теги !reset/!override — PyYAML их не знает."""


for _tag in ("!reset", "!override"):
    _ComposeLoader.add_constructor(_tag, lambda loader, node: loader.construct_sequence(node)
                                   if isinstance(node, yaml.SequenceNode) else loader.construct_scalar(node))

COMPOSE = yaml.load((ROOT / "compose.yml").read_text(), Loader=_ComposeLoader)
PROD = yaml.load((ROOT / "compose.prod.yml").read_text(), Loader=_ComposeLoader)
LIMITS_RAW = (ROOT / "compose.limits.yml").read_text()
CI = (ROOT / ".github/workflows/ci.yml").read_text()
BACKEND_DF = (ROOT / "backend/Dockerfile").read_text()
FRONTEND_DF = (ROOT / "frontend/Dockerfile").read_text()
BIDDER_DF = (ROOT / "bidder-go/Dockerfile").read_text()

PINNED = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")


def _external_images():
    for name, svc in {**COMPOSE["services"], **PROD["services"]}.items():
        img = svc.get("image")
        if img and not img.startswith("centraldsp/"):
            yield name, img


def test_all_external_compose_images_pinned_by_digest():
    bad = [(n, i) for n, i in _external_images() if not PINNED.match(i)]
    assert not bad, f"floating image tags: {bad}"


def test_all_dockerfile_base_images_pinned_by_digest():
    for df in (BACKEND_DF, FRONTEND_DF, BIDDER_DF):
        froms = re.findall(r"^FROM\s+(\S+)", df, flags=re.M)
        assert froms
        for ref in froms:
            assert PINNED.match(ref), f"unpinned base image: {ref}"


def test_postgres_major_is_15_everywhere():
    assert COMPOSE["services"]["postgres"]["image"].startswith("postgres:15.")
    ci_pg = re.findall(r"image:\s*postgres:(\d+)", CI)
    assert ci_pg and set(ci_pg) == {"15"}, f"CI postgres majors: {ci_pg}"


def test_backend_image_installs_from_hash_locked_requirements():
    assert "requirements.lock" in BACKEND_DF
    assert "--require-hashes" in BACKEND_DF and "--only-binary=:all:" in BACKEND_DF
    assert "pip install --no-cache-dir ." not in BACKEND_DF


def test_backend_entrypoint_drops_privileges():
    assert 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in BACKEND_DF
    ep = (ROOT / "backend/docker-entrypoint.sh").read_text()
    assert "exec setpriv --reuid=\"$APP_UID\" --regid=\"$APP_GID\" --clear-groups" in ep
    assert "--no-new-privs" in ep
    code_only = "\n".join(line for line in ep.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"chown\s+(-\w*R|--recursive)", code_only), \
        "никакого рекурсивного chown — только адресный find -xdev по uploads"
    assert "--timeout-graceful-shutdown" in BACKEND_DF


def test_backend_and_scheduler_healthchecks_run_as_app_user():
    for svc in ("backend", "scheduler"):
        test = COMPOSE["services"][svc]["healthcheck"]["test"]
        assert test[:5] == ["CMD", "setpriv", "--reuid=10001", "--regid=10001", "--clear-groups"], svc


def test_frontend_and_bidder_run_non_root():
    assert re.search(r"^USER node\s*$", FRONTEND_DF, flags=re.M)
    assert "distroless/static-debian12:nonroot@" in BIDDER_DF
    assert "--frozen-lockfile" in FRONTEND_DF


def test_reservations_everywhere_and_no_hard_limits_in_base_compose():
    for name, svc in {**COMPOSE["services"], **PROD["services"]}.items():
        if "image" not in svc and "build" not in svc:
            continue  # prod overlay без image (только restart/ports) — берётся из базы
        res = svc.get("deploy", {}).get("resources", {})
        if name in COMPOSE["services"] or "image" in svc:
            assert res.get("reservations", {}).get("memory"), f"{name}: no memory reservation"
        assert "limits" not in res, f"{name}: hard limits belong to compose.limits.yml only"


def test_limits_overlay_is_fully_parameterized_without_defaults():
    limits = yaml.safe_load(LIMITS_RAW)
    for name, svc in limits["services"].items():
        lim = svc["deploy"]["resources"]["limits"]
        for key in ("memory", "cpus"):
            val = str(lim[key])
            assert val.startswith("${LIMIT_") and ":?" in val, f"{name}.{key}: must be required var, got {val}"
            assert ":-" not in val, f"{name}.{key}: no silent default allowed"
    assert set(limits["services"]) == {"postgres", "redis", "backend", "scheduler", "bidder-go", "frontend"}


def test_stop_grace_covers_uvicorn_graceful_timeout():
    grace = COMPOSE["services"]["backend"]["stop_grace_period"]
    uvicorn_timeout = int(re.search(r"--timeout-graceful-shutdown (\d+)", BACKEND_DF).group(1))
    assert grace.endswith("s") and int(grace[:-1]) > uvicorn_timeout
