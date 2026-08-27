"""Geo reference autocomplete for targeting (countries, regions, cities)."""

from fastapi import APIRouter, Depends, Query

from auth import current_user
from geo_data import CITIES, COUNTRIES, RU_REGIONS

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
