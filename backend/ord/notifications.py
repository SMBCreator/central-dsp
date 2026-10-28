"""B13g: ошибки ЕРИР (ord_spec §7).

ОРД принимает объект синхронно (external_id, erid), а ЕРИР проверяет его позже
и присылает ошибку в ОРД. Ozon отдаёт их списком:
POST /api/external/notification/error_list {pageSize, gtNotificationId} →
{notification:[{id, objectId, externalId, objectType, text, date, updatedAt, url}]}.

Здесь: забор с курсором (platform_settings.ord_notification_cursor), запись в
ord_notifications, перевод объекта в статус erir_error с текстом ошибки и
обратный переход — resolve_for() при успешной повторной регистрации.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import obs_metrics

CURSOR_KEY = "ord_notification_cursor"
PAGE_SIZE = 100
MAX_PAGES = 50

OBJECT_TYPES = ("organisation", "contract", "creative", "invoice", "platform")

# objectType ОРД → (наша таблица, колонка внешнего id, колонка статуса, колонка ошибки, колонка владельца)
_TARGETS: dict[str, tuple[str, str, str, str, str | None]] = {
    "organisation": ("counterparties", "id::text", "ord_status", "ord_error", "user_id"),
    "contract": ("ord_contracts", "id::text", "ord_status", "ord_error", "user_id"),
    "creative": ("ord_creatives", "creative_id::text || ':v' || version::text", "status", "error", None),
    "invoice": ("ord_invoices", "id::text", "status", "error", "user_id"),
}


def normalize_object_type(raw: str | None) -> str:
    """objectType ОРД → organisation|contract|creative|invoice|platform, иначе unknown.

    Swagger: ERROR_NOTIFICATION_OBJECT_TYPE_CREATIVE и т.п. (в enum спеки — с лишней
    запятой в конце); короткая форма CREATIVE тоже принимается.
    """
    t = (raw or "").strip().rstrip(",").lower().removeprefix("error_notification_object_type_")
    return t if t in OBJECT_TYPES else "unknown"


def _parse_ts(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


async def load_cursor(conn, provider: str) -> int:
    raw = await conn.fetchval("SELECT value FROM platform_settings WHERE key = $1", CURSOR_KEY)
    if not raw:
        return 0
    data = raw if isinstance(raw, dict) else json.loads(raw)
    return int(data.get("last_id") or 0) if data.get("provider") == provider else 0


async def save_cursor(conn, provider: str, last_id: int) -> None:
    value = {"provider": provider, "last_id": int(last_id),
             "last_run_at": datetime.now(UTC).isoformat()}
    await conn.execute(
        """INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        CURSOR_KEY, json.dumps(value),
    )


async def _mark_object(conn, object_type: str, external_id: str | None, text: str) -> str | None:
    """Ставит erir_error на объект по нашему external_id; возвращает owner_id (для scope)."""
    target = _TARGETS.get(object_type)
    if not target or not external_id:
        return None
    table, ext_expr, status_col, error_col, owner_col = target
    owner_sel = f"{owner_col}" if owner_col else "NULL::uuid"
    row = await conn.fetchrow(
        f"""UPDATE {table}
               SET {status_col} = 'erir_error', {error_col} = $2, updated_at = now()
             WHERE {ext_expr} = $1
             RETURNING {owner_sel} AS owner_id""",
        external_id, text,
    )
    if row is None:
        return None
    if object_type == "creative":
        # владелец версии — владелец креатива
        return await conn.fetchval(
            "SELECT user_id FROM creatives WHERE id::text = split_part($1, ':v', 1)", external_id
        )
    return row["owner_id"]


async def apply_notification(conn, provider: str, n: dict[str, Any]) -> bool:
    """Записывает уведомление и помечает объект. Возвращает True, если уведомление новое."""
    object_type = normalize_object_type(n.get("object_type"))
    external_id = (n.get("external_id") or "").strip() or None
    text = (n.get("text") or "").strip()
    owner_id = await _mark_object(conn, object_type, external_id, text)
    tag = await conn.fetchval(
        """INSERT INTO ord_notifications
             (provider, notification_id, object_type, object_external_id, external_id, owner_id,
              text, url, ord_date, ord_updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           ON CONFLICT (provider, notification_id) DO UPDATE
             SET text = EXCLUDED.text, url = EXCLUDED.url, ord_updated_at = EXCLUDED.ord_updated_at,
                 owner_id = COALESCE(ord_notifications.owner_id, EXCLUDED.owner_id),
                 updated_at = now()
           RETURNING (xmax = 0) AS inserted""",
        provider, int(n["id"]), object_type, str(n.get("object_id") or "") or None, external_id,
        owner_id, text, n.get("url"), _parse_ts(n.get("date")), _parse_ts(n.get("updated_at")),
    )
    return bool(tag)


async def sync_notifications(pool, adapter, page_size: int = PAGE_SIZE) -> dict[str, Any]:
    """Забирает все новые уведомления постранично (gtNotificationId = курсор)."""
    provider = adapter.provider
    async with pool.acquire() as conn:
        cursor = await load_cursor(conn, provider)
    start = cursor
    fetched = new = 0
    pages = 0
    while pages < MAX_PAGES:
        batch = await adapter.list_error_notifications(gt_id=cursor, page_size=page_size)
        pages += 1
        if not batch:
            break
        async with pool.acquire() as conn, conn.transaction():
            for n in sorted(batch, key=lambda x: int(x["id"])):
                if await apply_notification(conn, provider, n):
                    new += 1
                fetched += 1
                cursor = max(cursor, int(n["id"]))
            await save_cursor(conn, provider, cursor)
        if len(batch) < page_size:
            break
    if fetched == 0:
        async with pool.acquire() as conn:
            await save_cursor(conn, provider, cursor)
    if new:
        obs_metrics.inc("ord_notification_errors_total", new)
    obs_metrics.set_gauge("ord_notifications_last_success_timestamp_seconds", time.time())
    async with pool.acquire() as conn:
        await refresh_gauges(conn)
    return {"provider": provider, "cursor_from": start, "cursor_to": cursor,
            "fetched": fetched, "new": new, "pages": pages}


async def resolve_for(conn, object_type: str, external_id: str, reason: str = "reregistered") -> int:
    """Успешная повторная регистрация объекта: закрыть его открытые уведомления."""
    tag = await conn.execute(
        """UPDATE ord_notifications SET resolved_at = now(), resolved_reason = $3, updated_at = now()
            WHERE object_type = $1 AND external_id = $2 AND resolved_at IS NULL""",
        object_type, external_id, reason,
    )
    try:
        return int(tag.rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def refresh_gauges(conn) -> dict[str, float]:
    row = await conn.fetchrow(
        """SELECT count(*) AS n,
                  COALESCE(EXTRACT(EPOCH FROM (now() - min(created_at))), 0) AS oldest
             FROM ord_notifications WHERE resolved_at IS NULL"""
    )
    n = int(row["n"]) if row else 0
    oldest = float(row["oldest"]) if row and n else 0.0
    obs_metrics.set_gauge("ord_notifications_unresolved", n)
    obs_metrics.set_gauge("ord_notifications_unresolved_oldest_age_seconds", oldest)
    return {"unresolved": n, "oldest_age_seconds": oldest}


async def list_open(conn, scope) -> list[dict[str, Any]]:
    """Открытые ошибки ЕРИР в scope пользователя (owner_id NULL = только платформенные роли)."""
    if scope.all:
        rows = await conn.fetch(
            "SELECT * FROM ord_notifications WHERE resolved_at IS NULL ORDER BY created_at DESC LIMIT 500"
        )
    else:
        rows = await conn.fetch(
            f"""SELECT * FROM ord_notifications
                 WHERE resolved_at IS NULL AND {scope.where('owner_id', 1)}
                 ORDER BY created_at DESC LIMIT 500""",
            *scope.params,
        )
    return [dict(r) for r in rows]
