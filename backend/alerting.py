"""Технические алерты (audit C4b / N-02): Alertmanager -> /alerts/webhook -> суперадмин.

Поток: Prometheus (ops/prometheus/alerts.yml) -> Alertmanager
(ops/alertmanager/alertmanager.yml) -> POST /alerts/webhook с Bearer
ALERT_WEBHOOK_SECRET -> `process_payload()`:

  1. каждый alert пишется в `alert_events` (ON CONFLICT DO NOTHING) — повторы
     Alertmanager (repeat_interval) не порождают дублей;
  2. только для новых строк: in-app уведомление каждому суперадмину и письмо
     в notification_outbox на адреса из настроек (`platform_settings.alerting`),
     при пустом списке — на email суперадминов;
  3. alertname=Watchdog (vector(1), всегда горит) не сохраняется — только
     обновляет gauge `alert_watchdog_last_received_ts`; по нему правило
     AlertPipelineDead ловит молчащий Alertmanager.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import notify_outbox
import obs_metrics

log = logging.getLogger("alerting")

SETTINGS_KEY = "alerting"
WATCHDOG_ALERTNAME = "Watchdog"
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
DEFAULT_SETTINGS: dict[str, Any] = {
    "emails": [],
    "email_min_severity": "warning",
    "email_on_resolve": True,
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IN_APP_LINK = "/admin/alerts"
# Alertmanager при repeat_interval 10m для Watchdog: тишина дольше часа = pipeline мёртв.
WATCHDOG_STALE_SECONDS = 3600


def webhook_secret() -> str:
    return os.getenv("ALERT_WEBHOOK_SECRET", "").strip()


def seed_watchdog() -> None:
    """При старте считаем pipeline живым: даём час на первый Watchdog, иначе
    AlertPipelineDead горел бы после каждого деплоя до первого heartbeat."""
    obs_metrics.set_gauge("alert_watchdog_last_received_ts", float(time.time()))


def watchdog_last_received() -> float:
    return float(obs_metrics.snapshot()["gauges"].get("alert_watchdog_last_received_ts", 0.0))


# ---- settings ------------------------------------------------------------------

def normalise_settings(raw: Any) -> dict[str, Any]:
    """Привести значение из platform_settings/тела запроса к каноническому виду.
    Неизвестные ключи отбрасываются, адреса — lower/strip/уникальные, невалидные
    выбрасываются с ValueError (PUT возвращает 400)."""
    src = raw if isinstance(raw, dict) else {}
    emails: list[str] = []
    for e in src.get("emails") or []:
        addr = str(e).strip().lower()
        if not addr:
            continue
        if not EMAIL_RE.match(addr):
            raise ValueError(f"Некорректный email: {addr}")
        if addr not in emails:
            emails.append(addr)
    if len(emails) > 20:
        raise ValueError("Не больше 20 адресов")
    sev = str(src.get("email_min_severity") or DEFAULT_SETTINGS["email_min_severity"]).lower()
    if sev not in SEVERITY_RANK:
        raise ValueError(f"email_min_severity: ожидается одно из {sorted(SEVERITY_RANK)}")
    return {
        "emails": emails,
        "email_min_severity": sev,
        "email_on_resolve": bool(src.get("email_on_resolve", DEFAULT_SETTINGS["email_on_resolve"])),
    }


async def load_settings(pool: Any) -> dict[str, Any]:
    raw = await pool.fetchval("SELECT value FROM platform_settings WHERE key = $1", SETTINGS_KEY)
    if raw is None:
        return dict(DEFAULT_SETTINGS)
    try:
        return normalise_settings(raw if isinstance(raw, dict) else json.loads(raw))
    except ValueError as e:  # в БД попало руками что-то кривое — не роняем алертинг
        log.warning("alerting settings invalid, using defaults: %s", e)
        return dict(DEFAULT_SETTINGS)


async def save_settings(pool: Any, settings: dict[str, Any], user_id: Any) -> None:
    await pool.execute(
        """INSERT INTO platform_settings (key, value, updated_by)
           VALUES ($1, $2::jsonb, $3)
           ON CONFLICT (key) DO UPDATE
             SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
        SETTINGS_KEY, json.dumps(settings), user_id,
    )


async def superadmin_ids_and_emails(pool: Any) -> tuple[list[Any], list[str]]:
    rows = await pool.fetch(
        "SELECT id, email FROM users WHERE role = 'superadmin' AND coalesce(status, 'active') = 'active'"
    )
    ids = [r["id"] for r in rows]
    emails = [str(r["email"]).strip().lower() for r in rows if r["email"] and EMAIL_RE.match(str(r["email"]).strip())]
    return ids, emails


async def resolve_recipients(pool: Any, settings: dict[str, Any] | None = None) -> tuple[list[str], str]:
    """Адреса для писем и откуда они взялись: 'settings' | 'superadmins' | 'none'."""
    settings = settings or await load_settings(pool)
    if settings["emails"]:
        return list(settings["emails"]), "settings"
    _, emails = await superadmin_ids_and_emails(pool)
    return emails, ("superadmins" if emails else "none")


# ---- payload ----------------------------------------------------------------------

def _parse_ts(value: Any) -> datetime | None:
    """Alertmanager шлёт RFC3339 с наносекундами и 'Z'; 0001-01-01 = «не задано»."""
    if not value or not isinstance(value, str) or value.startswith("0001-01-01"):
        return None
    s = value.strip().replace("Z", "+00:00")
    if "." in s:
        head, tail = s.split(".", 1)
        frac = "".join(ch for ch in tail if ch.isdigit())
        tz = tail[len(frac):]
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}" if frac else f"{head}{tz}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt_ts(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"


def render_email(alert: dict[str, Any]) -> tuple[str, str, str]:
    """(subject, text, html) для одного перехода алерта."""
    firing = alert["status"] == "firing"
    state = "FIRING" if firing else "RESOLVED"
    subject = f"{state} [{alert['severity']}] {alert['alertname']}"
    if alert["summary"]:
        subject += f": {alert['summary']}"
    lines = [
        f"{state}: {alert['alertname']} (severity={alert['severity']})",
        "",
        alert["summary"] or "",
        alert["description"] or "",
        "",
        f"Началось: {_fmt_ts(alert['starts_at'])}",
    ]
    if not firing:
        lines.append(f"Завершилось: {_fmt_ts(alert['ends_at'])}")
    labels = {k: v for k, v in alert["labels"].items() if k not in ("alertname", "severity")}
    if labels:
        lines += ["", "Метки: " + ", ".join(f"{k}={v}" for k, v in sorted(labels.items()))]
    if alert["external_url"]:
        lines += ["", f"Alertmanager: {alert['external_url']}"]
    text = "\n".join(lines).strip() + "\n"
    colour = "#b91c1c" if firing and alert["severity"] == "critical" else ("#b45309" if firing else "#15803d")
    html_body = (
        f"<p style='font:14px system-ui'><strong style='color:{colour}'>{state}</strong> "
        f"<strong>{html.escape(alert['alertname'])}</strong> "
        f"<span style='color:#6b7280'>severity={html.escape(alert['severity'])}</span></p>"
        f"<p style='font:14px system-ui'>{html.escape(alert['summary'])}</p>"
        f"<p style='font:13px system-ui;color:#374151;white-space:pre-wrap'>{html.escape(alert['description'])}</p>"
        f"<p style='font:12px system-ui;color:#6b7280'>Началось: {_fmt_ts(alert['starts_at'])}"
        + (f"<br>Завершилось: {_fmt_ts(alert['ends_at'])}" if not firing else "")
        + (("<br>Метки: " + html.escape(", ".join(f"{k}={v}" for k, v in sorted(labels.items())))) if labels else "")
        + "</p>"
    )
    return subject[:200], text, html_body


def parse_alert(raw: dict[str, Any], external_url: str | None) -> dict[str, Any] | None:
    labels = raw.get("labels") or {}
    annotations = raw.get("annotations") or {}
    alertname = str(labels.get("alertname") or "").strip()
    fingerprint = str(raw.get("fingerprint") or "").strip()
    status = str(raw.get("status") or "").strip().lower()
    starts_at = _parse_ts(raw.get("startsAt"))
    if not alertname or not fingerprint or status not in ("firing", "resolved") or starts_at is None:
        return None
    severity = str(labels.get("severity") or "warning").lower()
    if severity not in SEVERITY_RANK:
        severity = "warning"
    return {
        "fingerprint": fingerprint,
        "alertname": alertname,
        "severity": severity,
        "status": status,
        "starts_at": starts_at,
        "ends_at": _parse_ts(raw.get("endsAt")),
        "summary": str(annotations.get("summary") or "").strip(),
        "description": str(annotations.get("description") or "").strip(),
        "labels": {str(k): str(v) for k, v in labels.items()},
        "annotations": {str(k): str(v) for k, v in annotations.items()},
        "external_url": external_url,
    }


async def process_payload(pool: Any, payload: dict[str, Any]) -> dict[str, int]:
    """Обработать webhook Alertmanager (version 4). Возвращает счётчики для ответа/логов."""
    external_url = str(payload.get("externalURL") or "") or None
    result = {"received": 0, "stored": 0, "duplicates": 0, "invalid": 0, "watchdog": 0,
              "in_app": 0, "emails_queued": 0}
    settings = await load_settings(pool)
    superadmin_ids, superadmin_emails = await superadmin_ids_and_emails(pool)
    recipients = settings["emails"] or superadmin_emails
    min_rank = SEVERITY_RANK[settings["email_min_severity"]]

    for raw in payload.get("alerts") or []:
        result["received"] += 1
        alert = parse_alert(raw if isinstance(raw, dict) else {}, external_url)
        if alert is None:
            result["invalid"] += 1
            obs_metrics.inc("alert_webhook_invalid_total")
            continue
        if alert["alertname"] == WATCHDOG_ALERTNAME:
            obs_metrics.set_gauge("alert_watchdog_last_received_ts", float(time.time()))
            obs_metrics.inc("alert_webhook_watchdog_total")
            result["watchdog"] += 1
            continue
        row = await pool.fetchrow(
            """INSERT INTO alert_events
                   (fingerprint, alertname, severity, status, starts_at, ends_at, summary, description,
                    labels, annotations, external_url)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11)
               ON CONFLICT (fingerprint, status, starts_at) DO NOTHING
               RETURNING id""",
            alert["fingerprint"], alert["alertname"], alert["severity"], alert["status"],
            alert["starts_at"], alert["ends_at"], alert["summary"], alert["description"],
            json.dumps(alert["labels"]), json.dumps(alert["annotations"]), alert["external_url"],
        )
        if row is None:
            result["duplicates"] += 1
            obs_metrics.inc("alert_webhook_duplicate_total")
            continue
        result["stored"] += 1
        obs_metrics.inc("alert_webhook_stored_total")

        subject, text, html_body = render_email(alert)
        for uid in superadmin_ids:
            await pool.execute(
                "INSERT INTO notifications (user_id, type, title, body, link) VALUES ($1, $2, $3, $4, $5)",
                uid, "tech_alert", subject, (alert["description"] or alert["summary"])[:2000], IN_APP_LINK,
            )
            result["in_app"] += 1

        send_email = SEVERITY_RANK[alert["severity"]] >= min_rank and (
            alert["status"] == "firing" or settings["email_on_resolve"]
        )
        if send_email and recipients:
            result["emails_queued"] += await notify_outbox.enqueue_email(
                pool, recipients=recipients, subject=subject, body_text=text, body_html=html_body,
                kind="tech_alert", source="alertmanager",
                dedup_key=f"alert:{alert['fingerprint']}:{alert['status']}:{alert['starts_at'].isoformat()}",
                entity_type="alert", entity_id=alert["fingerprint"],
            )
    if result["stored"]:
        log.info("alertmanager webhook: %s", result)
    return result
