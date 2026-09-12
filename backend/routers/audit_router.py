"""Журнал действий (audit_log) — чтение для админов.

Таблица append-only, пишет AuditMiddleware (audit.py) на каждую мутацию.
Здесь только чтение: фильтры, курсорная пагинация по id, CSV-выгрузка.
Никаких DELETE/UPDATE — журнал нельзя редактировать даже суперадмину.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from db import get_pool
from rbac import require_admin

router = APIRouter(prefix="/audit", tags=["audit"])

# Известные сущности (первый сегмент пути) — для выпадающего фильтра в UI.
# Не читаем из БД distinct'ом: на большом журнале это seq scan.
ENTITY_LABELS: dict[str, str] = {
    "campaigns": "Кампании",
    "line-items": "Группы объявлений",
    "creatives": "Креативы",
    "advertisers": "Рекламодатели",
    "users": "Пользователи",
    "supply-partners": "SSP-партнёры",
    "rules": "Правила",
    "audiences": "Аудитории",
    "pixels": "Пиксели",
    "billing": "Биллинг",
    "ord": "ОРД",
    "moderation": "Модерация",
    "settings": "Настройки",
    "share": "Доступ клиентов",
    "groups": "Группы",
    "auth": "Сессии",
    "scoring": "Скоринг",
    "ai": "AI-инструменты",
}

MAX_LIMIT = 200
CSV_LIMIT = 50_000


def _where(
    *,
    entity: str | None,
    entity_id: str | None,
    user_email: str | None,
    method: str | None,
    status_min: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    before_id: int | None,
) -> tuple[str, list]:
    clauses: list[str] = []
    args: list = []

    def add(sql: str, value) -> None:
        args.append(value)
        clauses.append(sql.replace("?", f"${len(args)}"))

    if entity:
        add("entity = ?", entity)
    if entity_id:
        add("entity_id = ?", entity_id)
    if user_email:
        add("user_email ILIKE ?", f"%{user_email}%")
    if method:
        add("method = ?", method.upper())
    if status_min is not None:
        add("status_code >= ?", status_min)
    if date_from is not None:
        add("created_at >= ?", date_from)
    if date_to is not None:
        add("created_at < ?", date_to)
    if before_id is not None:
        add("id < ?", before_id)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


_SELECT = """
    SELECT id, user_id, user_email, user_role, method, path, status_code,
           entity, entity_id, ip, created_at
    FROM audit_log
"""


def _row(r) -> dict:
    return {
        "id": r["id"],
        "user_id": str(r["user_id"]) if r["user_id"] else None,
        "user_email": r["user_email"],
        "user_role": r["user_role"],
        "method": r["method"],
        "path": r["path"],
        "status_code": r["status_code"],
        "entity": r["entity"],
        "entity_id": r["entity_id"],
        "ip": r["ip"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


@router.get("/entities")
async def audit_entities(_user: dict = Depends(require_admin)):
    return [{"key": k, "label": v} for k, v in ENTITY_LABELS.items()]


@router.get("/log")
async def audit_log(
    _user: dict = Depends(require_admin),
    entity: str | None = Query(default=None, max_length=64),
    entity_id: str | None = Query(default=None, max_length=128),
    user_email: str | None = Query(default=None, max_length=200),
    method: str | None = Query(default=None, pattern="^(?i:post|put|patch|delete)$"),
    status_min: int | None = Query(default=None, ge=100, le=599),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
):
    """Страница журнала, новые сверху. Курсор — `before_id` (id последней строки
    предыдущей страницы); `next_before_id` = null, когда записи закончились."""
    where, args = _where(
        entity=entity, entity_id=entity_id, user_email=user_email, method=method,
        status_min=status_min, date_from=date_from, date_to=date_to, before_id=before_id,
    )
    pool = await get_pool()
    rows = await pool.fetch(
        f"{_SELECT}{where} ORDER BY id DESC LIMIT ${len(args) + 1}", *args, limit + 1
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_row(r) for r in rows],
        "next_before_id": rows[-1]["id"] if has_more and rows else None,
    }


@router.get("/log.csv")
async def audit_log_csv(
    _user: dict = Depends(require_admin),
    entity: str | None = Query(default=None, max_length=64),
    entity_id: str | None = Query(default=None, max_length=128),
    user_email: str | None = Query(default=None, max_length=200),
    method: str | None = Query(default=None, pattern="^(?i:post|put|patch|delete)$"),
    status_min: int | None = Query(default=None, ge=100, le=599),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    where, args = _where(
        entity=entity, entity_id=entity_id, user_email=user_email, method=method,
        status_min=status_min, date_from=date_from, date_to=date_to, before_id=None,
    )
    pool = await get_pool()
    rows = await pool.fetch(
        f"{_SELECT}{where} ORDER BY id DESC LIMIT ${len(args) + 1}", *args, CSV_LIMIT
    )
    buf = io.StringIO()
    # BOM — чтобы Excel на Windows открыл кириллицу без «кракозябр».
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "Время (UTC)", "Пользователь", "Роль", "Метод", "Путь", "Код", "Сущность", "ID сущности", "IP"])
    for r in rows:
        w.writerow([
            r["id"],
            r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else "",
            r["user_email"] or "",
            r["user_role"] or "",
            r["method"],
            r["path"],
            r["status_code"],
            r["entity"] or "",
            r["entity_id"] or "",
            r["ip"] or "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
