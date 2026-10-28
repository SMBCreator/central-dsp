"""GEO v2: иерархический гео-таргетинг по place_id (docs/GEO.md, план B.3).

Эталон для Go (bidder-go/internal/geo): та же нормализация (geo_norm), тот же
порядок сопоставления и та же семантика матчинга. Паритет — общие кейсы
bidder-go/internal/geo/testdata/fixtures.json.

Сопоставление запроса (resolve), по убыванию приоритета:
  регион: алиас SSP -> код ISO 3166-2 ("RU-MOW" или "MOW") -> название/синонимы;
  город:  алиас SSP -> название/синонимы в стране (при нескольких — в
          пришедшем регионе, иначе самый населённый) -> ближайший город по
          lat/lon в радиусе (только GEO_LATLON_ENABLED);
  результат — цепочка «место -> родители до страны» и уровень
  city | region | country | unknown.

Матчинг группы (match): исключения важнее включений; включение срабатывает,
если любое звено цепочки есть в geo_place_ids («выбрал город — только город,
выбрал регион — весь регион»). Неизвестное гео: geo_unknown=strict (по
умолчанию) — не торгуем, lenient — торгуем.

Флаги (по умолчанию выключены):
  GEO_V2_ENABLED     — v2-ключи работают; при выключенном флаге группа с
                       geo_place_ids/geo_exclude_place_ids не матчится
                       (fail-closed: не крутим «везде» вместо выбранного гео).
  GEO_LATLON_ENABLED — ближайший город по координатам, GEO_LATLON_RADIUS_KM (25).
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

from country_codes import normalize_country
from geo_norm import geo_normalize

MAX_GEO_PLACES = 500
UNKNOWN_MODES = ("strict", "lenient")
LATLON_CELL_DEG = 0.5
DEFAULT_LATLON_RADIUS_KM = 25.0
_INDEX_RECHECK_SECONDS = 30.0
# geo_places.aliases начинается с нормализованных name_ru и name_en (ops/geo_import.py).
PRIMARY_NAMES = 2

LEVEL_CITY, LEVEL_REGION, LEVEL_COUNTRY, LEVEL_UNKNOWN = "city", "region", "country", "unknown"
_CITY_TYPES = ("city", "town")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def geo_v2_enabled() -> bool:
    # Включён по умолчанию: справочник приезжает с образом (geo_seed.py).
    # GEO_V2_ENABLED=0 — аварийное выключение.
    return _env_flag("GEO_V2_ENABLED", default=True)


def latlon_enabled() -> bool:
    return _env_flag("GEO_LATLON_ENABLED")


def latlon_radius_km() -> float:
    try:
        return float(os.environ.get("GEO_LATLON_RADIUS_KM", "") or DEFAULT_LATLON_RADIUS_KM)
    except ValueError:
        return DEFAULT_LATLON_RADIUS_KM


def has_geo_v2(targeting: dict) -> bool:
    return bool(targeting.get("geo_place_ids") or targeting.get("geo_exclude_place_ids"))


@dataclass(frozen=True)
class Resolved:
    chain: tuple[int, ...] = ()
    level: str = LEVEL_UNKNOWN
    # Значения, которые не удалось сопоставить (для метрики/пополнения алиасов).
    unmatched_region: str = ""
    unmatched_city: str = ""


UNKNOWN = Resolved()


@dataclass
class _Place:
    id: int
    cc: str
    type: str
    parent: int
    lat: float | None
    lon: float | None
    pop: int


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / LATLON_CELL_DEG), math.floor(lon / LATLON_CELL_DEG))


class GeoIndex:
    """Иммутабельный индекс справочника. Строится из строк фида (feed_places)."""

    def __init__(self, places: list[dict], aliases: list[dict], version: str = ""):
        self.version = version
        self.places: dict[int, _Place] = {}
        self.country: dict[str, int] = {}
        self.region_code: dict[str, int] = {}
        self.region_name: dict[tuple[str, str], int] = {}
        self.city_name: dict[tuple[str, str], list[int]] = {}
        self.alias: dict[tuple[str, str, str], int] = {}
        self.grid: dict[tuple[int, int], list[int]] = {}
        region_rank: dict[tuple[str, str], tuple[int, int]] = {}

        for row in places:
            p = _Place(int(row["id"]), row["cc"], row["t"], int(row.get("p") or 0),
                       row.get("lat"), row.get("lon"), int(row.get("pop") or 0))
            self.places[p.id] = p
            if p.type == "country":
                self.country[p.cc] = p.id
            elif p.type == "region":
                if row.get("rc"):
                    self.region_code[row["rc"].upper()] = p.id
                for i, name in enumerate(row.get("n") or ()):
                    key = (p.cc, name)
                    # Первые PRIMARY_NAMES синонимов — основные названия (ru, en):
                    # они важнее синонимов другого региона ("moscow" у Московской
                    # области). При равенстве — меньший id, чтобы не зависеть от порядка.
                    rank = (0 if i < PRIMARY_NAMES else 1, p.id)
                    if key not in region_rank or rank < region_rank[key]:
                        region_rank[key] = rank
                        self.region_name[key] = p.id
            elif p.type in _CITY_TYPES:
                for name in row.get("n") or ():
                    self.city_name.setdefault((p.cc, name), []).append(p.id)
                if p.lat is not None and p.lon is not None:
                    self.grid.setdefault(_cell(p.lat, p.lon), []).append(p.id)
        for ids in self.city_name.values():
            ids.sort(key=lambda i: (-self.places[i].pop, i))
        for a in aliases:
            if int(a["place_id"]) in self.places:
                self.alias[(str(a["partner_id"]), a["kind"], a["value_norm"])] = int(a["place_id"])

    # --- resolve ---

    def _chain(self, place_id: int) -> tuple[int, ...]:
        out: list[int] = []
        cur = place_id
        while cur and cur in self.places and len(out) < 4:
            out.append(cur)
            cur = self.places[cur].parent
        return tuple(out)

    def _region(self, partner: str, cc: str, raw: str) -> int:
        norm = geo_normalize(raw)
        if not norm:
            return 0
        if pid := self.alias.get((partner, "region", norm)):
            return pid
        code = raw.strip().upper()
        if "-" not in code and cc:
            code = f"{cc}-{code}"
        if (pid := self.region_code.get(code)) and (not cc or self.places[pid].cc == cc):
            return pid
        return self.region_name.get((cc, norm), 0) if cc else 0

    def _city(self, partner: str, cc: str, raw: str, region_id: int) -> int:
        norm = geo_normalize(raw)
        if not norm:
            return 0
        if pid := self.alias.get((partner, "city", norm)):
            return pid
        cands = self.city_name.get((cc, norm)) if cc else None
        if not cands:
            return 0
        if region_id:
            for pid in cands:
                if self.places[pid].parent == region_id:
                    return pid
        return cands[0]

    def _nearest(self, cc: str, lat: float, lon: float, radius_km: float) -> int:
        best: tuple[float, int, int] | None = None
        ci, cj = _cell(lat, lon)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for pid in self.grid.get((ci + di, cj + dj), ()):
                    p = self.places[pid]
                    if cc and p.cc != cc:
                        continue
                    d = _haversine_km(lat, lon, p.lat, p.lon)
                    if d <= radius_km:
                        key = (round(d, 6), -p.pop, pid)
                        if best is None or key < best:
                            best = key
        return best[2] if best else 0

    def resolve(self, partner: str, country: str | None, region: str | None, city: str | None,
                lat: float | None = None, lon: float | None = None,
                use_latlon: bool = False, radius_km: float = DEFAULT_LATLON_RADIUS_KM) -> Resolved:
        cc = normalize_country(country)
        partner = str(partner or "")
        country_id = self.country.get(cc, 0)
        region_id = self._region(partner, cc, region) if region else 0
        if region_id and not cc:
            cc = self.places[region_id].cc
            country_id = self.country.get(cc, 0)
        city_id = self._city(partner, cc, city, region_id) if city else 0
        if (not city_id and use_latlon and lat is not None and lon is not None
                and not (lat == 0 and lon == 0)):
            city_id = self._nearest(cc, float(lat), float(lon), radius_km)
        unmatched_region = region if region and not region_id else ""
        unmatched_city = city if city and not city_id else ""
        if city_id:
            return Resolved(self._chain(city_id), LEVEL_CITY, unmatched_region, "")
        if region_id:
            return Resolved(self._chain(region_id), LEVEL_REGION, "", unmatched_city)
        if country_id:
            return Resolved((country_id,), LEVEL_COUNTRY, unmatched_region, unmatched_city)
        return Resolved((), LEVEL_UNKNOWN, unmatched_region, unmatched_city)


def match_geo_v2(targeting: dict, resolved: Resolved | None, index: GeoIndex | None = None) -> bool:
    """Исключения важнее включений; включение — по любому звену цепочки.
    lenient: гео не определено вовсе ИЛИ определено не до конца, но найденное
    место содержит одно из выбранных (SSP прислала только страну RU, таргет —
    Краснодарский край). Второе требует index (предки мест включения)."""
    include = targeting.get("geo_place_ids") or []
    exclude = targeting.get("geo_exclude_place_ids") or []
    if not include and not exclude:
        return True
    lenient = targeting.get("geo_unknown") == "lenient"
    res = resolved or UNKNOWN
    if res.level == LEVEL_UNKNOWN:
        return lenient
    if exclude and any(pid in exclude for pid in res.chain):
        return False
    if not include:
        return True
    if any(pid in include for pid in res.chain):
        return True
    if lenient and index is not None and res.level != LEVEL_CITY and res.chain:
        deepest = res.chain[0]
        return any(deepest in index._chain(pid)[1:] for pid in include)
    return False


def resolve_request(index: GeoIndex | None, partner_id: str, req: dict) -> Resolved:
    if index is None:
        return UNKNOWN
    geo = (req.get("device") or {}).get("geo") or {}
    return index.resolve(
        partner_id, geo.get("country"), geo.get("region"), geo.get("city"),
        geo.get("lat"), geo.get("lon"), latlon_enabled(), latlon_radius_km(),
    )


# --- данные из БД (фид для Go и индекс Python-эталона) ---


async def feed_version(pool) -> str | None:
    """Версия = активный справочник + состояние алиасов. Меняется — Go
    перезагружает /rtb-feed/geo; иначе гео-данные не гоняются каждые 30 с."""
    row = await pool.fetchrow(
        """SELECT (SELECT version FROM geo_dataset_versions WHERE is_active) AS v,
                  (SELECT count(*) FROM geo_ssp_alias) AS n,
                  (SELECT COALESCE(extract(epoch FROM max(updated_at)), 0)::bigint FROM geo_ssp_alias) AS ts"""
    )
    if not row or not row["v"]:
        return None
    return f"{row['v']}:{row['n']}:{row['ts']}"


async def feed_payload(pool) -> dict:
    version = await feed_version(pool)
    rows = await pool.fetch(
        "SELECT id, country_iso, type, parent_id, region_code, aliases, lat, lon, population FROM geo_places"
    )
    places = []
    for r in rows:
        p = {"id": r["id"], "cc": r["country_iso"].strip(), "t": r["type"], "p": r["parent_id"] or 0,
             "n": list(r["aliases"] or [])}
        if r["region_code"]:
            p["rc"] = r["region_code"]
        if r["type"] in _CITY_TYPES and r["lat"] is not None:
            p["lat"], p["lon"], p["pop"] = r["lat"], r["lon"], r["population"]
        places.append(p)
    aliases = [
        {"partner_id": str(a["partner_id"]), "kind": a["kind"], "value_norm": a["value_norm"], "place_id": a["place_id"]}
        for a in await pool.fetch("SELECT partner_id, kind, value_norm, place_id FROM geo_ssp_alias")
    ]
    return {"version": version, "places": places, "aliases": aliases}


_cache: dict = {"index": None, "version": None, "checked": 0.0}


async def get_index(pool) -> GeoIndex | None:
    """Индекс для Python-эталона hot path: перестраивается только при смене версии."""
    now = time.monotonic()
    if _cache["index"] is not None and now - _cache["checked"] < _INDEX_RECHECK_SECONDS:
        return _cache["index"]
    _cache["checked"] = now
    version = await feed_version(pool)
    if version is None:
        _cache.update(index=None, version=None)
        return None
    if version != _cache["version"]:
        payload = await feed_payload(pool)
        _cache.update(index=GeoIndex(payload["places"], payload["aliases"], version), version=version)
    return _cache["index"]
