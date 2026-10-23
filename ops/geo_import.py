#!/usr/bin/env python3
"""GEO v2: импорт справочника мест в geo_places (docs/GEO.md, план B.3 п.2).

Источники:
  * GeoNames (CC BY 4.0, https://www.geonames.org): countryInfo, admin1CodesASCII,
    cities1000 (РФ) / cities15000 (остальные страны), alternateNamesV2 (ru/en/abbr).
  * Wikidata (CC0): коды ISO 3166-2 регионов (P300) по GeoNames ID (P1566).

Объём по умолчанию: РФ — все субъекты и населённые пункты от 1000 жителей,
остальной мир — только страны (--world-regions / --world-min-population
добавляют регионы и города других стран).

Справочник в репозитории (backend/data/geo_places.seed.jsonl.gz) собирается
  python ops/geo_import.py --export backend/data/geo_places.seed.jsonl.gz
и загружается сам при старте backend (backend/geo_seed.py).

Идемпотентность и версии:
  * id места = geonameid, поэтому повторный импорт обновляет строки, а
    geo_place_ids в таргетинге остаются валидными.
  * Версия = geonames-YYYYMMDD-<sha8>, где sha считается по входным файлам и
    правилам импорта. Если версия с тем же sha уже активна, импорт ничего не делает.
  * Строки, которых нет в новой версии, не удаляются: на них могут ссылаться
    таргеты и geo_ssp_alias. У них остаётся прежний dataset_version.

Запуск (тем же Python, что и backend: нужен asyncpg):
  python ops/geo_import.py --dsn "$DATABASE_URL"                # скачать и загрузить
  python ops/geo_import.py --dsn ... --data-dir /var/cache/geo  # кэш файлов
  python ops/geo_import.py --data-dir ... --offline --dry-run   # без сети и БД: статистика
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from geo_norm import geo_normalize  # noqa: E402
from geo_seed import COLUMNS, load_records  # noqa: E402

GEONAMES_BASE = "https://download.geonames.org/export/dump/"
GEONAMES_FILES = (
    "countryInfo.txt",
    "admin1CodesASCII.txt",
    "cities1000.zip",
    "cities15000.zip",
    "alternateNamesV2.zip",
)
WIKIDATA_FILE = "wikidata_iso3166_2.csv"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_QUERY = "SELECT ?gn ?iso WHERE { ?item wdt:P300 ?iso ; wdt:P1566 ?gn . }"
USER_AGENT = "central-dsp-geo-import/1.0 (ops/geo_import.py)"

SOURCE = "geonames+wikidata"
LICENSE = "GeoNames CC BY 4.0 (https://www.geonames.org); ISO 3166-2: Wikidata CC0"

# Меняйте при любом изменении правил ниже или geo_norm: это меняет sha версии.
RULES_VERSION = "3"

# Части населённых пунктов, исторические и заброшенные — не места для таргетинга.
EXCLUDED_FCODES = frozenset({"PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"})
CITY_FCODES = frozenset({"PPLC", "PPLA"})
CITY_MIN_POPULATION = 100_000
MAX_ALIASES = 40

# Субъекты, которые текущий UI (frontend/components/geo-panel.tsx, RU_REGIONS)
# показывает в составе РФ, а GeoNames ведёт под UA. Импорт повторяет UI, чтобы
# существующие таргеты переносились без потерь. ISO 3166-2 кода у них нет.
RU_UI_OVERRIDES: dict[str, dict] = {
    "UA.11": {"name_ru": "Республика Крым", "aliases": ["Крым", "Республика Крым", "Crimea", "Republic of Crimea"]},
    "UA.20": {"name_ru": "Севастополь", "aliases": ["Севастополь", "Sevastopol", "Sevastopol City"]},
}

# Общеупотребительные сокращения, которых нет в GeoNames (geonameid -> синонимы).
EXTRA_ALIASES: dict[int, list[str]] = {
    524894: ["Moskva", "MSK", "МСК", "Moscow City"],  # Москва (субъект)
    524901: ["MSK", "МСК"],  # Москва (город)
    536203: ["SPB", "СПб", "Питер", "Sankt-Peterburg", "St Petersburg"],  # Санкт-Петербург (субъект)
    498817: ["SPB", "СПб", "Питер"],  # Санкт-Петербург (город)
}

@dataclass
class Place:
    id: int
    country_iso: str
    type: str
    name_en: str
    parent_id: int | None = None
    region_code: str | None = None
    lat: float | None = None
    lon: float | None = None
    population: int = 0
    extra_names: list[str] = field(default_factory=list)  # ascii / GeoNames name
    name_ru: str | None = None
    ru_override: dict | None = None


@dataclass
class AltNames:
    ru: list[tuple[int, str]] = field(default_factory=list)  # (rank, name): меньше = лучше
    en_preferred: str | None = None
    other: list[str] = field(default_factory=list)  # en / abbr


# ---------- файлы ----------


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=600) as resp, tmp.open("wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.replace(dest)


def fetch_inputs(data_dir: Path, offline: bool, refresh: bool) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in GEONAMES_FILES:
        path = data_dir / name
        if path.exists() and not refresh:
            continue
        if offline:
            raise SystemExit(f"--offline: нет {path}")
        print(f"download {name}", flush=True)
        _download(GEONAMES_BASE + name, path)
    wd = data_dir / WIKIDATA_FILE
    if (refresh or not wd.exists()) and not offline:
        url = WIKIDATA_SPARQL + "?" + urllib.parse.urlencode({"query": WIKIDATA_QUERY})
        print("download wikidata ISO 3166-2", flush=True)
        try:
            _download(url, wd)
        except OSError as exc:
            if not wd.exists():
                raise SystemExit(f"Wikidata недоступна и кэша нет: {exc}") from exc
            print(f"wikidata: {exc}; использую кэш {wd}", flush=True)
    if not wd.exists():
        raise SystemExit(f"--offline: нет {wd}")


def inputs_sha(data_dir: Path) -> str:
    h = hashlib.sha256(f"rules={RULES_VERSION}\n".encode())
    h.update(Path(__file__).with_name("geo_import.py").read_bytes())
    h.update((Path(__file__).resolve().parent.parent / "backend" / "geo_norm.py").read_bytes())
    for name in (*GEONAMES_FILES, WIKIDATA_FILE):
        with (data_dir / name).open("rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
    return h.hexdigest()


def _tsv(lines):
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        yield line.rstrip("\n").split("\t")


def _zip_lines(path: Path):
    with zipfile.ZipFile(path) as z:
        member = path.stem + ".txt"  # alternateNamesV2.zip содержит ещё iso-languagecodes.txt
        with z.open(member) as raw:
            yield from io.TextIOWrapper(raw, encoding="utf-8")


# ---------- разбор ----------


def load_iso_codes(path: Path) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                gid = int(row["gn"])
            except (KeyError, ValueError):
                continue
            out.setdefault(gid, []).append(row["iso"].strip().upper())
    return out


def build_places(
    data_dir: Path, ru_min_pop: int, world_min_pop: int, world_regions: bool = False
) -> tuple[list[Place], dict]:
    places: dict[int, Place] = {}
    country_gid: dict[str, int] = {}
    stats: dict[str, int] = {}

    with (data_dir / "countryInfo.txt").open(encoding="utf-8") as f:
        for r in _tsv(f):
            if len(r) < 17 or not r[16].strip():
                continue
            gid = int(r[16])
            cc = r[0].strip().upper()
            country_gid[cc] = gid
            places[gid] = Place(id=gid, country_iso=cc, type="country", name_en=r[4].strip(),
                                population=int(r[7] or 0), extra_names=[r[1].strip()])
    stats["countries"] = len(country_gid)

    iso = load_iso_codes(data_dir / WIKIDATA_FILE)
    admin1_gid: dict[str, int] = {}
    with (data_dir / "admin1CodesASCII.txt").open(encoding="utf-8") as f:
        for r in _tsv(f):
            code, name, ascii_name, gid = r[0], r[1], r[2], int(r[3])
            cc = code.split(".", 1)[0]
            override = RU_UI_OVERRIDES.get(code)
            if override:
                cc = "RU"
            if cc not in country_gid or (cc != "RU" and not world_regions):
                continue
            admin1_gid[code] = gid
            region_code = None
            if not override:
                codes = sorted(c for c in iso.get(gid, []) if c.startswith(cc + "-"))
                region_code = codes[0] if codes else None
            places[gid] = Place(id=gid, country_iso=cc, type="region", name_en=name, parent_id=country_gid[cc],
                                region_code=region_code, extra_names=[ascii_name], ru_override=override)
    ru_regions = [p for p in places.values() if p.type == "region" and p.country_iso == "RU"]
    missing = [p.name_en for p in ru_regions if not p.region_code and not p.ru_override]
    if missing:
        raise SystemExit(f"нет ISO 3166-2 для регионов РФ: {missing} (обновите {WIKIDATA_FILE})")
    seen_codes: dict[str, int] = {}
    for p in places.values():
        if p.region_code:
            if p.region_code in seen_codes:  # один код у двух мест — не доверяем ни одному
                places[seen_codes[p.region_code]].region_code = None
                p.region_code = None
                stats["region_code_conflicts"] = stats.get("region_code_conflicts", 0) + 1
            else:
                seen_codes[p.region_code] = p.id
    stats["regions"] = sum(1 for p in places.values() if p.type == "region")
    stats["regions_with_iso"] = sum(1 for p in places.values() if p.type == "region" and p.region_code)

    def add_cities(fname: str, want) -> None:
        for r in _tsv(_zip_lines(data_dir / fname)):
            gid = int(r[0])
            if gid in places or r[7] in EXCLUDED_FCODES:
                continue
            cc, admin1, pop = r[8], r[10], int(r[14] or 0)
            key = f"{cc}.{admin1}"
            if key in RU_UI_OVERRIDES:
                cc = "RU"
            if cc not in country_gid or not want(cc, pop):
                continue
            kind = "city" if (r[7] in CITY_FCODES or pop >= CITY_MIN_POPULATION) else "town"
            places[gid] = Place(
                id=gid, country_iso=cc, type=kind, name_en=r[1], parent_id=admin1_gid.get(key, country_gid[cc]),
                lat=float(r[4]), lon=float(r[5]), population=pop, extra_names=[r[2]],
            )

    add_cities("cities1000.zip", lambda cc, pop: cc == "RU" and pop >= ru_min_pop)
    if world_min_pop > 0:
        add_cities("cities15000.zip", lambda cc, pop: cc != "RU" and pop >= world_min_pop)
    stats["cities"] = sum(1 for p in places.values() if p.type in ("city", "town"))
    stats["cities_ru"] = sum(1 for p in places.values() if p.type in ("city", "town") and p.country_iso == "RU")

    alts: dict[int, AltNames] = {}
    t0 = time.time()
    for line in _zip_lines(data_dir / "alternateNamesV2.zip"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 8:
            continue
        lang = parts[2]
        # "" — названия без языка: среди них транслитерации ("Moskva"), которые
        # шлют SSP. Берём только ASCII, чтобы не тащить десятки письменностей.
        if lang not in ("ru", "en", "abbr", "") or (lang == "" and not parts[3].isascii()):
            continue
        gid = int(parts[1])
        if gid not in places:
            continue
        name, preferred, short, colloquial, historic = parts[3], parts[4] == "1", parts[5] == "1", parts[6] == "1", parts[7] == "1"
        if historic or not name.strip():
            continue
        a = alts.setdefault(gid, AltNames())
        if lang == "ru":
            rank = (0 if preferred else 2) + (1 if short else 0) + (4 if colloquial else 0)
            a.ru.append((rank, name.strip()))
        elif lang == "" and (len(name) < 3 or any(ch.isdigit() for ch in name)):
            continue  # коды и номера вида "RU-48", "1": это не названия
        else:
            if lang == "en" and preferred and not short and a.en_preferred is None:
                a.en_preferred = name.strip()
            a.other.append(name.strip())
    stats["alt_names_seconds"] = int(time.time() - t0)

    for p in places.values():
        a = alts.get(p.id)
        if a and a.en_preferred and p.type != "region":
            p.extra_names.append(p.name_en)
            p.name_en = a.en_preferred
        if p.ru_override:
            p.name_ru = p.ru_override["name_ru"]
        elif a and a.ru:
            p.name_ru = min(a.ru, key=lambda x: (x[0], len(x[1])))[1] if p.type != "region" else _region_ru(a.ru)
        else:
            p.name_ru = p.name_en
    stats["without_ru_name"] = sum(1 for p in places.values() if p.name_ru == p.name_en and p.country_iso == "RU")
    return list(places.values()), {"alts": alts, "stats": stats}


def _region_ru(ru: list[tuple[int, str]]) -> str:
    # Для регионов полное название («Республика Татарстан») лучше короткого
    # («Татарстан»): так их хранит текущий UI и старые таргеты.
    full = [x for x in ru if x[0] % 2 == 0] or ru
    name = min(full, key=lambda x: (x[0], -len(x[1])))[1]
    return name.replace(" Область", " область").replace(" Край", " край")


def aliases_for(p: Place, a: AltNames | None) -> list[str]:
    ordered: list[str] = [p.name_ru or "", p.name_en, *p.extra_names]
    if p.ru_override:
        ordered += p.ru_override["aliases"]
    ordered += EXTRA_ALIASES.get(p.id, [])
    if a:
        ordered += [n for _, n in sorted(a.ru)] + a.other
    out: list[str] = []
    for raw in ordered:
        n = geo_normalize(raw)
        if n and n not in out:
            out.append(n)
        if len(out) >= MAX_ALIASES:
            break
    return out


def to_records(places: list[Place], alts: dict[int, AltNames], version: str) -> list[tuple]:
    rows = []
    for p in sorted(places, key=lambda x: x.id):
        name_ru = p.name_ru or p.name_en
        rows.append((
            p.id, p.country_iso, p.region_code, name_ru, p.name_en, geo_normalize(name_ru), geo_normalize(p.name_en),
            aliases_for(p, alts.get(p.id)), p.type, p.lat, p.lon, p.population, p.parent_id, "geonames", version,
        ))
    return rows


# ---------- БД ----------


async def load_db(dsn: str, records: list[tuple], version: str, sha: str, stats: dict) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://"))
    try:
        active = await conn.fetchrow("SELECT version, meta->>'sha256' AS sha FROM geo_dataset_versions WHERE is_active")
        if active and active["sha"] == sha:
            print(f"версия {active['version']} уже активна (sha совпадает) — ничего не делаю")
            return
        # origin=manual: автозагрузка seed при старте backend эту версию не перезапишет.
        await load_records(conn, records, version, SOURCE, LICENSE, {"sha256": sha, "stats": stats, "origin": "manual"})
        print(f"импортирована версия {version}: {len(records)} мест")
    finally:
        await conn.close()


def export_seed(path: Path, records: list[tuple], version: str, sha: str, stats: dict) -> None:
    header = {"version": version, "sha256": sha, "source": SOURCE, "license": LICENSE,
              "stats": stats, "columns": list(COLUMNS)}
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0: одинаковые входы дают байт-в-байт одинаковый файл (чистый git diff).
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write((json.dumps(header, ensure_ascii=False) + "\n").encode())
        for r in records:
            gz.write((json.dumps(list(r), ensure_ascii=False, separators=(",", ":")) + "\n").encode())
    print(f"seed записан: {path} ({path.stat().st_size // 1024} КБ, {len(records)} мест)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", help="DATABASE_URL; без него только --dry-run")
    ap.add_argument("--data-dir", default=".geo-cache", type=Path)
    ap.add_argument("--offline", action="store_true", help="не скачивать, взять файлы из --data-dir")
    ap.add_argument("--refresh", action="store_true", help="перекачать файлы, даже если они есть в кэше")
    ap.add_argument("--dry-run", action="store_true", help="разобрать и вывести статистику, без записи в БД")
    ap.add_argument("--export", type=Path, help="записать seed-файл (jsonl.gz) вместо загрузки в БД")
    ap.add_argument("--ru-min-population", type=int, default=1000)
    ap.add_argument("--world-min-population", type=int, default=0, help="0 — без городов вне РФ")
    ap.add_argument("--world-regions", action="store_true", help="регионы (admin1) стран вне РФ")
    args = ap.parse_args(argv)
    if not args.dry_run and not args.dsn and not args.export:
        ap.error("нужен --dsn, --export или --dry-run")

    fetch_inputs(args.data_dir, args.offline, args.refresh)
    params = f"ru>={args.ru_min_population};world>={args.world_min_population};world_regions={args.world_regions}"
    sha = hashlib.sha256((inputs_sha(args.data_dir) + params).encode()).hexdigest()
    version = f"geonames-{date.today():%Y%m%d}-{sha[:8]}"
    places, extra = build_places(args.data_dir, args.ru_min_population, args.world_min_population, args.world_regions)
    records = to_records(places, extra["alts"], version)
    stats = extra["stats"] | {"places": len(records)}
    print(json.dumps({"version": version, **stats}, ensure_ascii=False))
    if args.dry_run:
        return 0
    if args.export:
        export_seed(args.export, records, version, sha, stats)
        return 0
    asyncio.run(load_db(args.dsn, records, version, sha, stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
