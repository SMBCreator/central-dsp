"""Технические алерты (audit C4b / N-02).

POST /alerts/webhook — приёмник Alertmanager (Bearer ALERT_WEBHOOK_SECRET, без
cookie: CSRF и аудит-мидлвари его пропускают как /cron/*).
GET  /alerts/events  — лента переходов для страницы «Алерты» суперадмина плюс
состояние pipeline (когда последний раз приходил Watchdog).
GET  /alerts/outbox  — последние письма из notification_outbox со статусом
доставки (N-04: видно, что ушло, что в retry, что failed и почему).
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

import alerting
import notify_outbox
from db import get_pool
from rbac import require_roles

router = APIRouter(prefix="/alerts", tags=["alerts"])
_require_superadmin = require_roles("superadmin")


def _check_webhook_secret(request: Request) -> None:
    secret = alerting.webhook_secret()
    if not secret:
        # Как в /cron/*: без секрета в production ручка закрыта, в dev открыта.
        if os.environ.get("APP_ENV", "").strip().lower() in ("production", "prod"):
            raise HTTPException(status_code=503, detail="ALERT_WEBHOOK_SECRET is not configured")
        return
    if request.headers.get("authorization", "") != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="invalid webhook token")


@router.post("/webhook")
async def alertmanager_webhook(request: Request):
    _check_webhook_secret(request)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise HTTPException(status_code=400, detail="expected Alertmanager webhook payload with 'alerts' list")
    if len(payload["alerts"]) > 500:
        raise HTTPException(status_code=413, detail="too many alerts in one request")
    return await alerting.process_payload(await get_pool(), payload)


@router.get("/events")
async def list_alert_events(
    limit: int = Query(100, ge=1, le=500),
    status: str | None = Query(None, pattern="^(firing|resolved)$"),
    user: dict = Depends(_require_superadmin),
):
    pool = await get_pool()
    if status:
        rows = await pool.fetch(
            """SELECT id, fingerprint, alertname, severity, status, starts_at, ends_at, summary, description,
                      labels, external_url, received_at
               FROM alert_events WHERE status = $1 ORDER BY received_at DESC LIMIT $2""",
            status, limit,
        )
    else:
        rows = await pool.fetch(
            """SELECT id, fingerprint, alertname, severity, status, starts_at, ends_at, summary, description,
                      labels, external_url, received_at
               FROM alert_events ORDER BY received_at DESC LIMIT $1""",
            limit,
        )
    # Активные = алерты, чей последний переход — firing.
    active = await pool.fetch(
        """SELECT fingerprint, alertname, severity, starts_at, summary FROM (
               SELECT DISTINCT ON (fingerprint) fingerprint, alertname, severity, status, starts_at, summary
               FROM alert_events ORDER BY fingerprint, received_at DESC
           ) last WHERE status = 'firing' ORDER BY starts_at DESC"""
    )
    last_wd = alerting.watchdog_last_received()
    age = time.time() - last_wd if last_wd else None
    return {
        "events": [dict(r) for r in rows],
        "active": [dict(r) for r in active],
        "pipeline": {
            "webhook_secret_configured": bool(alerting.webhook_secret()),
            "smtp_configured": notify_outbox.is_configured(),
            "watchdog_last_received_at": last_wd or None,
            "watchdog_age_seconds": age,
            "healthy": age is not None and age < alerting.WATCHDOG_STALE_SECONDS,
        },
    }


@router.get("/outbox")
async def list_outbox(
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None, pattern="^(queued|sending|sent|failed)$"),
    user: dict = Depends(_require_superadmin),
):
    """Последние письма outbox: адрес, вид, тема, статус, попытки, последняя ошибка.
    Тело письма не отдаём — в нём могут быть данные кампаний других пользователей."""
    pool = await get_pool()
    where = "WHERE status = $2" if status else ""
    args = [limit, status] if status else [limit]
    rows = await pool.fetch(
        f"""SELECT id, channel, recipient, kind, subject, status, attempts, next_attempt_at, last_error,
                   source, entity_type, entity_id, created_at, sent_at
            FROM notification_outbox {where} ORDER BY id DESC LIMIT $1""",
        *args,
    )
    counts = await pool.fetch("SELECT status, count(*)::int AS n FROM notification_outbox GROUP BY status")
    return {"items": [dict(r) for r in rows], "counts": {r["status"]: r["n"] for r in counts}}
