"""Geo reference for targeting.

/geo/search — старый autocomplete (статические списки geo_data, ключи
geo_countries/geo_regions/geo_cities). /geo/config и /geo/places* — GEO v2
(справочник geo_places, ключи geo_place_ids/geo_exclude_place_ids), см. docs/GEO.md.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import geo_v2
from auth import current_user
from db import get_pool
from geo_data import CITIES, COUNTRIES, RU_REGIONS
from geo_norm import geo_normalize

router = APIRouter(prefix="/geo", tags=["geo"])


def _match(q: str, *names: str) -> bool:
    return any(n.lower().startswith(q) or q in n.lower() for n in names if n)


@router.get("/search")
async def geo_search(
    q: str = Query(min_length=2, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    user: dict = Depends(current_user),
):
    """Search countries/regions/cities by RU or EN prefix (min 2 chars)."""
    query = q.strip().lower()
    out: list[dict] = []

    for label_ru, label_en, code in COUNTRIES:
        if _match(query, label_ru, label_en, code):
            out.append({"kind": "country", "label": label_ru, "value": code, "hint": code})
        if len(out) >= limit:
            return out

    for name in RU_REGIONS:
        if _match(query, name):
            out.append({"kind": "region", "label": name, "value": name, "hint": "RU"})
        if len(out) >= limit:
            return out

    for label_ru, label_en, cc in CITIES:
        if _match(query, label_ru, label_en):
            out.append({"kind": "city", "label": label_ru, "value": label_ru, "hint": cc})
        if len(out) >= limit:
            return out

    return out


# ---------------- GEO v2 ----------------

_SELECT = """
    SELECT p.id, p.type, p.name_ru, p.name_en, p.country_iso, p.region_code, p.parent_id, p.population,
           pp.name_ru AS parent_name, ppp.name_ru AS grandparent_name
      FROM geo_places p
      LEFT JOIN geo_places pp ON pp.id = p.parent_id
      LEFT JOIN geo_places ppp ON ppp.id = pp.parent_id
"""
_TYPE_ORDER = "CASE p.type WHEN 'country' THEN 0 WHEN 'region' THEN 1 WHEN 'city' THEN 2 ELSE 3 END"
_TYPES = {"country", "region", "city", "town"}


def _place_out(r) -> dict:
    path = ", ".join(x for x in (r["parent_name"], r["grandparent_name"]) if x)
    return {
        "id": r["id"], "type": r["type"], "name_ru": r["name_ru"], "name_en": r["name_en"],
        "country_iso": r["country_iso"].strip(), "region_code": r["region_code"],
        "parent_id": r["parent_id"], "population": r["population"], "path": path,
    }


def _types(raw: str | None) -> list[str]:
    if not raw:
        return sorted(_TYPES)
    types = [t.strip() for t in raw.split(",") if t.strip()]
    if not types or any(t not in _TYPES for t in types):
        raise HTTPException(422, "type: country, region, city, town")
    return types


def _country(raw: str | None) -> str | None:
    if not raw:
        return None
    cc = raw.strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        raise HTTPException(422, "country: ISO 3166-1 alpha-2")
    return cc


@router.get("/config")
async def geo_config(user: dict = Depends(current_user)):
    pool = await get_pool()
    version = await pool.fetchval("SELECT version FROM geo_dataset_versions WHERE is_active")
    return {
        "v2_enabled": geo_v2.geo_v2_enabled(),
        "dataset_version": version,
        "max_places": geo_v2.MAX_GEO_PLACES,
    }


@router.get("/places/search")
async def places_search(
    q: str = Query(min_length=2, max_length=64),
    country: str | None = Query(default=None, max_length=2),
    type: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=1000),
    user: dict = Depends(current_user),
):
    """Autocomplete по справочнику: префикс русского/английского названия или
    точный синоним. Сначала точные совпадения, затем страны → регионы → города,
    внутри — по населению."""
    norm = geo_normalize(q)
    if len(norm) < 2:
        return {"items": [], "has_more": False}
    pattern = norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    pool = await get_pool()
    rows = await pool.fetch(
        _SELECT + f"""
         WHERE (p.name_ru_norm LIKE $1 OR p.name_en_norm LIKE $1 OR p.aliases @> ARRAY[$2]::text[])
           AND p.type = ANY($3::text[])
           AND ($4::text IS NULL OR p.country_iso = $4)
         ORDER BY (p.name_ru_norm = $2 OR p.name_en_norm = $2) DESC, {_TYPE_ORDER}, p.population DESC, p.id
         LIMIT $5 OFFSET $6""",
        pattern, norm, _types(type), _country(country), limit + 1, offset,
    )
    return {"items": [_place_out(r) for r in rows[:limit]], "has_more": len(rows) > limit}


@router.get("/places/{place_id}/children")
async def places_children(
    place_id: int,
    type: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
    user: dict = Depends(current_user),
):
    """Дочерние места (регионы страны, города региона) для иерархического выбора."""
    norm = geo_normalize(q) if q else ""
    pattern = (norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%") if norm else None
    pool = await get_pool()
    rows = await pool.fetch(
        _SELECT + f"""
         WHERE p.parent_id = $1 AND p.type = ANY($2::text[])
           AND ($3::text IS NULL OR p.name_ru_norm LIKE $3 OR p.name_en_norm LIKE $3)
         ORDER BY {_TYPE_ORDER}, p.population DESC, p.name_ru, p.id
         LIMIT $4 OFFSET $5""",
        place_id, _types(type), pattern, limit + 1, offset,
    )
    return {"items": [_place_out(r) for r in rows[:limit]], "has_more": len(rows) > limit}


@router.get("/places")
async def places_by_ids(
    ids: str = Query(max_length=20000, description="через запятую"),
    user: dict = Depends(current_user),
):
    """Подписи выбранных мест (чипы в форме таргетинга)."""
    try:
        id_list = sorted({int(x) for x in ids.split(",") if x.strip()})
    except ValueError as exc:
        raise HTTPException(422, "ids: целые числа через запятую") from exc
    if len(id_list) > 2 * geo_v2.MAX_GEO_PLACES:
        raise HTTPException(422, f"ids: не больше {2 * geo_v2.MAX_GEO_PLACES}")
    if not id_list:
        return []
    pool = await get_pool()
    rows = await pool.fetch(_SELECT + " WHERE p.id = ANY($1::bigint[])", id_list)
    return [_place_out(r) for r in rows]


class ResolveIn(BaseModel):
    lines: list[str] = Field(max_length=500)
    country: str = Field(default="RU", min_length=2, max_length=2)


@router.post("/places/resolve")
async def places_resolve(body: ResolveIn, user: dict = Depends(current_user)):
    """Массовое добавление: список названий (по строке) -> места страны.

    found — одно совпадение; ambiguous — несколько (например, «Москва» —
    и субъект, и город): UI предлагает первый вариант и даёт выбрать;
    not_found — нет в справочнике."""
    cc = _country(body.country)
    inputs = [line.strip() for line in body.lines if line.strip()]
    norms = sorted({geo_normalize(x) for x in inputs} - {""})
    by_norm: dict[str, list[dict]] = {n: [] for n in norms}
    if norms:
        pool = await get_pool()
        rows = await pool.fetch(
            """
            SELECT q.n, p.id, p.type, p.name_ru, p.name_en, p.country_iso, p.region_code, p.parent_id,
                   p.population, pp.name_ru AS parent_name, ppp.name_ru AS grandparent_name
              FROM unnest($1::text[]) AS q(n)
              JOIN geo_places p ON p.aliases @> ARRAY[q.n] AND p.country_iso = $2 AND p.type <> 'country'
              LEFT JOIN geo_places pp ON pp.id = p.parent_id
              LEFT JOIN geo_places ppp ON ppp.id = pp.parent_id
             ORDER BY q.n, CASE p.type WHEN 'region' THEN 0 WHEN 'city' THEN 1 ELSE 2 END, p.population DESC, p.id""",
            norms, cc,
        )
        for r in rows:
            if len(by_norm[r["n"]]) < 5:
                by_norm[r["n"]].append(_place_out(r))
    out = []
    for raw in inputs:
        cands = by_norm.get(geo_normalize(raw), [])
        status = "not_found" if not cands else ("found" if len(cands) == 1 else "ambiguous")
        out.append({"input": raw, "status": status, "candidates": cands})
    return out
