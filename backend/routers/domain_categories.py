"""Справочник «домен → тематика IAB»: кабинет администратора (Фаза 3 ТЗ).

Только admin/superadmin (`require_admin`): клиентам — 403. Фид для биддера —
отдельно, в routers/bidder_feed.py (`/rtb-feed/domain-categories`).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import domain_categories as dc
from db import get_pool
from rbac import require_admin

router = APIRouter(prefix="/domain-categories", tags=["domain-categories"])


class DomainBody(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    iab_codes: list[str] = Field(min_length=1, max_length=dc.MAX_MANUAL_CODES + 5)


class ImportBody(BaseModel):
    csv: str = Field(max_length=2_000_000)
    apply: bool = False


def _validated(body: DomainBody) -> tuple[str, list[str]]:
    domain = dc.normalize_domain(body.domain)
    if domain is None:
        raise HTTPException(422, "Некорректное имя домена")
    codes, bad = dc.normalize_codes(body.iab_codes)
    if bad:
        raise HTTPException(422, "Недопустимые коды IAB: " + ", ".join(bad))
    if not codes:
        raise HTTPException(422, "Укажите хотя бы один код IAB")
    return domain, codes


@router.get("")
async def list_domains(
    q: str = Query(default="", max_length=100),
    source: str | None = Query(default=None, pattern="^(manual|learned)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user: dict = Depends(require_admin),
):
    return await dc.list_domains(await get_pool(), q=q, source=source, limit=limit, offset=offset)


@router.put("")
async def put_domain(body: DomainBody, _user: dict = Depends(require_admin)):
    """Создать или изменить запись; любая правка делает её ручной (manual)."""
    domain, codes = _validated(body)
    return await dc.upsert_manual(await get_pool(), domain, codes)


@router.delete("/{domain}")
async def delete_domain(domain: str, _user: dict = Depends(require_admin)):
    d = dc.normalize_domain(domain)
    if d is None:
        raise HTTPException(422, "Некорректное имя домена")
    if not await dc.delete_domain(await get_pool(), d):
        raise HTTPException(404, "Домен не найден")
    return {"ok": True}


@router.post("/import")
async def import_csv(body: ImportBody, _user: dict = Depends(require_admin)):
    """CSV `domain;IAB1,IAB2`. apply=false — только предпросмотр с ошибками;
    apply=true применяет, но лишь когда ошибок нет (всё или ничего)."""
    rows, errors = dc.parse_import_csv(body.csv)
    result = {"valid": len(rows), "errors": errors, "applied": False}
    if not body.apply:
        result["preview"] = rows[:50]
        return result
    if errors:
        raise HTTPException(422, {"message": "В файле есть ошибки; исправьте и повторите", **result})
    result.update(await dc.import_rows(await get_pool(), rows))
    result["applied"] = True
    return result
