"""GEO v2: загрузка справочника мест в geo_places (docs/GEO.md).

Справочник лежит в репозитории (data/geo_places.seed.jsonl.gz, собран
ops/geo_import.py --export) и загружается при старте backend после migrate.py.
Сеть и ручные шаги не нужны: после деплоя выбор гео в кабинете сразу работает.

Когда загружается:
  * активной версии нет — загружаем;
  * активна прошлая версия из seed, а в репозитории новая — обновляем;
  * активна версия, загруженная вручную (ops/geo_import.py), — не трогаем.
Повторный старт с тем же seed ничего не делает (сверка sha256).

GEO_SEED_AUTOLOAD=0 — не загружать при старте.
Запуск вручную: python geo_seed.py [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
from pathlib import Path

SEED_PATH = Path(__file__).resolve().parent / "data" / "geo_places.seed.jsonl.gz"

COLUMNS = (
    "id", "country_iso", "region_code", "name_ru", "name_en", "name_ru_norm", "name_en_norm",
    "aliases", "type", "lat", "lon", "population", "parent_id", "source", "dataset_version",
)

# Один загрузчик на кластер, даже если стартуют несколько backend-реплик.
_ADVISORY_LOCK = 0x6E05EED


def read_seed(path: Path = SEED_PATH) -> tuple[dict, list[tuple]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = json.loads(f.readline())
        rows = [tuple(json.loads(line)) for line in f if line.strip()]
    if header.get("columns") != list(COLUMNS):
        raise ValueError(f"{path}: колонки seed не совпадают с geo_places")
    return header, rows


async def load_records(conn, records: list[tuple], version: str, source: str, license_: str, meta: dict) -> None:
    async with conn.transaction():
        await conn.execute("DROP TABLE IF EXISTS pg_temp.geo_places_in")
        await conn.execute("CREATE TEMP TABLE geo_places_in (LIKE geo_places INCLUDING DEFAULTS) ON COMMIT DROP")
        await conn.copy_records_to_table("geo_places_in", records=records, columns=list(COLUMNS))
        # Коды ISO могут переехать между местами — освобождаем их до upsert.
        await conn.execute("UPDATE geo_places SET region_code = NULL WHERE region_code IS NOT NULL")
        cols = ", ".join(COLUMNS)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "id")
        await conn.execute(
            f"INSERT INTO geo_places ({cols}) SELECT {cols} FROM geo_places_in "
            f"ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = now()"
        )
        await conn.execute("UPDATE geo_dataset_versions SET is_active = false WHERE is_active")
        await conn.execute(
            """INSERT INTO geo_dataset_versions (version, source, license, places_count, is_active, meta)
               VALUES ($1, $2, $3, $4, true, $5::jsonb)
               ON CONFLICT (version) DO UPDATE SET is_active = true, places_count = EXCLUDED.places_count,
                   meta = EXCLUDED.meta, imported_at = now()""",
            version, source, license_, len(records), json.dumps(meta, ensure_ascii=False),
        )


def _dsn(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")


async def apply_seed_conn(conn, header: dict, rows: list[tuple], force: bool = False) -> tuple[bool, str]:
    active = await conn.fetchrow(
        "SELECT version, meta->>'sha256' AS sha, meta->>'origin' AS origin FROM geo_dataset_versions WHERE is_active"
    )
    if active and not force:
        if active["sha"] == header["sha256"]:
            return False, f"geo seed: версия {active['version']} уже активна"
        if active["origin"] != "seed":
            return False, f"geo seed: активна версия {active['version']} (импорт вручную) — seed не применяю"
    meta = {"sha256": header["sha256"], "stats": header.get("stats", {}), "origin": "seed"}
    await load_records(conn, rows, header["version"], header["source"], header["license"], meta)
    return True, f"geo seed: загружена версия {header['version']}: {len(rows)} мест"


async def apply_seed(dsn: str, path: Path = SEED_PATH, force: bool = False) -> str:
    import asyncpg

    header, rows = read_seed(path)
    conn = await asyncpg.connect(_dsn(dsn))
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK)
        return (await apply_seed_conn(conn, header, rows, force))[1]
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="загрузить seed поверх любой активной версии")
    args = ap.parse_args(argv)
    if os.environ.get("GEO_SEED_AUTOLOAD", "1").strip().lower() in ("0", "false", "no", "off") and not args.force:
        print("geo seed: GEO_SEED_AUTOLOAD=0 — пропускаю", flush=True)
        return 0
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn or not SEED_PATH.exists():
        print(f"geo seed: нет DATABASE_URL или {SEED_PATH.name} — пропускаю", flush=True)
        return 0
    try:
        print(asyncio.run(apply_seed(dsn, force=args.force)), flush=True)
    except Exception as exc:  # noqa: BLE001 — старт backend важнее справочника
        # Без справочника группы с выбранными местами не покупают (fail-closed),
        # остальное работает. Причина видна в логе.
        print(f"geo seed: ошибка загрузки: {exc!r}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
