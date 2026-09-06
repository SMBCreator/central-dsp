"""Audit M-03: Redis holds trading money — eviction is fatal in production.

* startup_checks.redis_durability_problems: allkeys-lru => problem, noeviction
  => none; missing AOF => warning only.
* check_redis_durability against a fake client raises in production, warns in dev.
* docker-compose pins noeviction + AOF + volume and a single REDIS_URL for
  backend and bidder-go.
* Go mirrors the safe policy constant.
"""
import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import startup_checks as sc  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("policy", ["allkeys-lru", "volatile-lru", "allkeys-random", "volatile-ttl", "allkeys-lfu"])
def test_eviction_policy_is_a_problem(policy):
    problems, _ = sc.redis_durability_problems({}, {"maxmemory-policy": policy, "appendonly": "yes"})
    assert problems and policy in problems[0]


def test_noeviction_with_aof_is_clean():
    problems, warnings = sc.redis_durability_problems({"aof_enabled": "1"}, {"maxmemory-policy": "noeviction", "appendonly": "yes"})
    assert problems == [] and warnings == []


def test_no_persistence_is_a_warning_not_a_problem():
    problems, warnings = sc.redis_durability_problems(
        {"aof_enabled": "0", "rdb_last_save_time": "0"}, {"maxmemory-policy": "noeviction", "appendonly": "no"})
    assert problems == [] and len(warnings) == 1


class _FakeRedis:
    def __init__(self, policy: str):
        self.policy = policy

    async def config_get(self, name):
        return {name: {"maxmemory-policy": self.policy, "maxmemory": "0", "appendonly": "yes"}[name]}

    async def info(self, _section):
        return {"aof_enabled": 1}


def test_check_raises_in_production_and_warns_in_dev():
    with pytest.raises(RuntimeError):
        asyncio.run(sc.check_redis_durability(_FakeRedis("allkeys-lru"), production=True))
    asyncio.run(sc.check_redis_durability(_FakeRedis("allkeys-lru"), production=False))
    asyncio.run(sc.check_redis_durability(_FakeRedis("noeviction"), production=True))


class _BrokenRedis:
    async def config_get(self, name):
        raise PermissionError("CONFIG disabled by provider")

    async def info(self, _section):
        return {}


def test_check_survives_config_forbidden():
    """Managed Redis may forbid CONFIG — that must not block startup."""
    asyncio.run(sc.check_redis_durability(_BrokenRedis(), production=True))


def test_compose_redis_is_durable_and_shared():
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    redis_block = compose.split("  redis:", 1)[1].split("\n  backend:", 1)[0]
    assert "--maxmemory-policy noeviction" in redis_block
    assert "--appendonly yes" in redis_block
    assert "redis_data:/data" in redis_block
    assert "allkeys-lru" not in compose
    go_block = compose.split("  bidder-go:", 1)[1].split("\n  frontend:", 1)[0]
    go_url = re.search(r"^\s+REDIS_URL:\s*(\S+)", go_block, re.M)
    backend_block = compose.split("\n  backend:", 1)[1].split("\n  bidder:", 1)[0]
    be_url = re.search(r"^\s+REDIS_URL:\s*(\S+)", backend_block, re.M)
    assert go_url and be_url and go_url.group(1) == be_url.group(1), \
        "bidder-go must use the same REDIS_URL expression as backend"
    assert re.search(r"\n\s+redis_data:", compose), "redis_data volume must be declared"


def test_go_safe_policy_matches_python():
    go_src = (REPO / "bidder-go" / "internal" / "redisx" / "redisx.go").read_text(encoding="utf-8")
    m = re.search(r'const SafeMaxmemoryPolicy = "([a-z-]+)"', go_src)
    assert m and m.group(1) == sc.SAFE_MAXMEMORY_POLICY
