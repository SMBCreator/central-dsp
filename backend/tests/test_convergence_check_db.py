"""scripts/convergence_check.py на живом Postgres.

Сценарий 1: события и агрегат согласованы -> PASS, exit 0.
Сценарий 2: в stats_hourly «потерялся» один показ -> расхождение найдено, exit 1.
Окно берётся далеко в прошлом (2001 год), чтобы не пересекаться с чужими данными.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

asyncpg = pytest.importorskip("asyncpg")
DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL не задан")

from tests.test_idor_isolation_db import _Tenant  # noqa: E402

SINCE = datetime(2001, 1, 1, 0, 0, tzinfo=timezone.utc)
UNTIL = datetime(2001, 1, 1, 3, 0, tzinfo=timezone.utc)
HOUR = datetime(2001, 1, 1, 1, 0, tzinfo=timezone.utc)


def _run(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/convergence_check.py", "--since", SINCE.isoformat(), "--until", UNTIL.isoformat(), "--json", *extra],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True, text=True, env={**os.environ, "DATABASE_URL": DSN},
    )


async def _seed(conn, t: _Tenant, lost_impressions: int):
    ids = t.ids
    for _ in range(5):
        await conn.execute(
            """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id,
                                       price, is_test, created_at)
               VALUES ('bid', $1, $2, $3, $4, 100, false, $5)""",
            str(uuid.uuid4()), ids["camp"], ids["li"], ids["cr"], HOUR,
        )
    # Как пишут реальные win_notice/impression (E-01): clear_price несёт win,
    # показ — событие доставки без цены.
    for _ in range(2):
        bid_id = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id,
                                       clear_price, is_test, created_at)
               VALUES ('win', $1, $2, $3, $4, 900, false, $5)""",
            bid_id, ids["camp"], ids["li"], ids["cr"], HOUR,
        )
        await conn.execute(
            """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id,
                                       is_test, created_at)
               VALUES ('impression', $1, $2, $3, $4, false, $5)""",
            bid_id, ids["camp"], ids["li"], ids["cr"], HOUR,
        )
    # тестовый win — не должен учитываться ни с одной стороны
    await conn.execute(
        """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id,
                                   clear_price, is_test, created_at)
           VALUES ('win', $1, $2, $3, $4, 900, true, $5)""",
        str(uuid.uuid4()), ids["camp"], ids["li"], ids["cr"], HOUR,
    )
    imps = 2 - lost_impressions
    await conn.execute(
        """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id,
                                     bids, wins, impressions, clicks, spend)
           VALUES ($1, $2, $3, $4, 5, 2, $5, 0, $6)""",
        HOUR, ids["camp"], ids["li"], ids["cr"], imps, 2 * 900 / 1000,
    )


async def _cleanup(conn, t: _Tenant):
    await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", t.ids["camp"])
    await conn.execute("DELETE FROM stats_hourly WHERE campaign_id = $1", t.ids["camp"])
    await t.teardown(conn)


def _scenario(lost_impressions: int):
    async def go():
        conn = await asyncpg.connect(DSN)
        t = _Tenant("conv")
        try:
            await t.setup(conn)
            await _seed(conn, t, lost_impressions)
            return _run()
        finally:
            await _cleanup(conn, t)
            await conn.close()

    return asyncio.run(go())


def test_consistent_data_passes():
    r = _scenario(lost_impressions=0)
    assert r.returncode == 0, r.stdout + r.stderr
    assert '"pass": true' in r.stdout
    assert '"impressions": 2.0' in r.stdout  # is_test-показ не посчитан


def test_lost_impression_is_detected():
    r = _scenario(lost_impressions=1)
    assert r.returncode == 1, r.stdout + r.stderr
    assert '"hours_with_diff": 1' in r.stdout
    assert '"impressions"' in r.stdout and '"delta": -1.0' in r.stdout
