"""erid-гейт (38-ФЗ) и GEO v2: группа, выбранная только на зарубежные места,
не требует erid; любое место в РФ, неизвестный id или geo_unknown=lenient — требует."""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402

from ord.service import _NOT_RU_ONLY_SQL, campaign_targets_ru  # noqa: E402

KZ, MOSCOW, UNKNOWN = 990100001, 990100002, 990100999

CASES = {
    "v2_foreign": ({"geo_place_ids": [KZ]}, True),
    "v2_ru_city": ({"geo_place_ids": [MOSCOW]}, False),
    "v2_mixed": ({"geo_place_ids": [KZ, MOSCOW]}, False),
    "v2_unknown_id": ({"geo_place_ids": [UNKNOWN]}, False),
    "v2_foreign_lenient": ({"geo_place_ids": [KZ], "geo_unknown": "lenient"}, False),
    "v2_exclude_only": ({"geo_exclude_place_ids": [MOSCOW]}, False),
    "legacy_foreign": ({"geo_countries": ["KZ"]}, True),
    "legacy_ru": ({"geo_countries": ["RU"]}, False),
    "no_geo": ({}, False),
    "legacy_foreign_and_v2_ru": ({"geo_countries": ["KZ"], "geo_place_ids": [MOSCOW]}, True),
}


async def _seed(conn) -> dict:
    await conn.execute(
        """INSERT INTO geo_places (id, country_iso, name_ru, name_en, name_ru_norm, name_en_norm, type, dataset_version)
           VALUES ($1,'KZ','КЗ-тест','KZ-test','кз тест','kz test','country','erid-test'),
                  ($2,'RU','Мск-тест','Msk-test','мск тест','msk test','city','erid-test')
           ON CONFLICT (id) DO NOTHING""",
        KZ, MOSCOW,
    )
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','erid-v2','admin') RETURNING id",
        f"erid-v2-{uuid.uuid4()}@test.local",
    )
    adv = await conn.fetchval(
        "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1,'adv','active',1000) RETURNING id", uid
    )
    camps, lis = {}, {}
    for name, (targeting, _) in CASES.items():
        camps[name] = await conn.fetchval(
            """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
               VALUES ($1,$2,$3,'active',100,0) RETURNING id""",
            adv, uid, name,
        )
        lis[name] = await conn.fetchval(
            """INSERT INTO line_items (campaign_id, user_id, name, bid_price, status, targeting)
               VALUES ($1,$2,$3,1,'active',$4::jsonb) RETURNING id""",
            camps[name], uid, name, json.dumps(targeting),
        )
    return {"user": uid, "camps": camps, "lis": lis}


def test_erid_gate_understands_geo_v2():
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        s = await _seed(conn)
        try:
            for name, (_, not_ru_only) in CASES.items():
                gate_passes = await conn.fetchval(
                    f"SELECT {_NOT_RU_ONLY_SQL} FROM line_items li WHERE li.id = $1", s["lis"][name]
                )
                assert gate_passes is not_ru_only, name
                assert await campaign_targets_ru(pool, str(s["camps"][name])) is (not not_ru_only), name
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", s["user"])
            await conn.execute("DELETE FROM geo_places WHERE id IN ($1, $2)", KZ, MOSCOW)
            await pool.close()
            await conn.close()

    asyncio.run(scenario())
