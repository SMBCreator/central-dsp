"""GEO v2: справочник в репозитории (data/geo_places.seed.jsonl.gz) и его
автозагрузка при старте backend (geo_seed.py)."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geo_seed  # noqa: E402
from geo_norm import geo_normalize  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def seed():
    return geo_seed.read_seed()


def _col(name: str) -> int:
    return geo_seed.COLUMNS.index(name)


def test_seed_shape(seed):
    header, rows = seed
    assert header["version"].startswith("geonames-") and len(header["sha256"]) == 64
    assert len({r[0] for r in rows}) == len(rows)
    for r in rows:
        assert all(r[i] is not None for i, c in enumerate(geo_seed.COLUMNS) if c in ("id", "country_iso", "name_ru", "type"))


def test_seed_parents_resolve(seed):
    _, rows = seed
    ids = {r[0] for r in rows}
    orphans = [r for r in rows if r[_col("parent_id")] is not None and r[_col("parent_id")] not in ids]
    assert not orphans


def test_seed_covers_russia_in_detail_and_world_by_country(seed):
    _, rows = seed
    by_type: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r[_col("country_iso")] == "RU" and "RU" or "world", r[_col("type")])
        by_type[key] = by_type.get(key, 0) + 1
    assert by_type[("world", "country")] >= 240
    assert set(t for (scope, t) in by_type if scope == "world") == {"country"}
    assert by_type[("RU", "region")] == 85
    assert by_type[("RU", "city")] + by_type[("RU", "town")] >= 3000


@pytest.mark.parametrize("name", ["Москва", "Санкт-Петербург", "Сочи", "Краснодар", "Казань", "Кропоткин", "Ялта"])
def test_seed_has_ru_cities(seed, name):
    _, rows = seed
    assert any(r[_col("name_ru_norm")] == geo_normalize(name) and r[_col("type")] in ("city", "town") for r in rows)


def test_ru_regions_have_iso_codes(seed):
    _, rows = seed
    regions = [r for r in rows if r[_col("country_iso")] == "RU" and r[_col("type")] == "region"]
    no_code = sorted(r[_col("name_ru")] for r in regions if not r[_col("region_code")])
    # Крым и Севастополь: в UI — субъекты РФ, кода ISO 3166-2 RU-* у них нет.
    assert no_code == ["Республика Крым", "Севастополь"]


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_apply_seed_rules():
    import asyncpg

    def header(sha: str) -> dict:
        return {"version": f"seed-test-{sha}", "sha256": sha, "source": "test", "license": "test",
                "columns": list(geo_seed.COLUMNS)}

    rows = [
        (990000001, "ZZ", None, "Тестляндия", "Testland", "тестляндия", "testland", ["тестляндия", "testland"],
         "country", None, None, 0, None, "geonames", "seed-test"),
        (990000002, "ZZ", "ZZ-01", "Тест-регион", "Test Region", "тест регион", "test region", ["тест регион"],
         "region", None, None, 0, 990000001, "geonames", "seed-test"),
    ]

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        tx = conn.transaction()
        await tx.start()
        try:
            prev = await conn.fetchval("SELECT version FROM geo_dataset_versions WHERE is_active")
            await conn.execute("UPDATE geo_dataset_versions SET is_active = false WHERE is_active")

            assert (await geo_seed.apply_seed_conn(conn, header("a" * 64), rows))[0] is True
            assert await conn.fetchval("SELECT count(*) FROM geo_places WHERE id >= 990000001") == 2
            assert (await geo_seed.apply_seed_conn(conn, header("a" * 64), rows))[0] is False
            # Новый seed в репозитории поверх прошлого seed — обновляем.
            assert (await geo_seed.apply_seed_conn(conn, header("b" * 64), rows))[0] is True
            assert await conn.fetchval("SELECT meta->>'origin' FROM geo_dataset_versions WHERE is_active") == "seed"
            # Версия, загруженная вручную, автозагрузкой не перезаписывается.
            await conn.execute(
                "UPDATE geo_dataset_versions SET meta = meta || '{\"origin\": \"manual\"}'::jsonb WHERE is_active"
            )
            assert (await geo_seed.apply_seed_conn(conn, header("c" * 64), rows))[0] is False
            assert (await geo_seed.apply_seed_conn(conn, header("c" * 64), rows, force=True))[0] is True
            assert prev is None or prev != "seed-test-" + "c" * 64
        finally:
            await tx.rollback()
            await conn.close()

    asyncio.run(scenario())


def test_autoload_can_be_disabled(monkeypatch, capsys):
    monkeypatch.setenv("GEO_SEED_AUTOLOAD", "0")
    assert geo_seed.main([]) == 0
    assert "GEO_SEED_AUTOLOAD=0" in capsys.readouterr().out
