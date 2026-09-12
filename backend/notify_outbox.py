"""Ядро доставки уведомлений (audit C4b / N-01).

Одна таблица `notification_outbox` для всех внешних каналов. Продюсер пишет
строку через `enqueue_email()` и возвращается — SMTP в запрос не попадает.
Отправляет `dispatch()` из /cron/notify-dispatch (scheduler, раз в минуту):
претензия строк `FOR UPDATE SKIP LOCKED`, отправка, исход в той же строке.

SMTP — stdlib `smtplib` в `asyncio.to_thread`: dispatcher и так фоновый, а
лишняя зависимость ради async-сокета не нужна. Реальный отправитель
подменяется в тестах (`sender=`).

Без SMTP_HOST строки копятся в `queued` (`notify_smtp_configured` = 0 →
алерт NotifySmtpNotConfigured), ничего не теряется: после настройки
dispatcher отправит хвост.
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Awaitable, Callable

import obs_metrics

log = logging.getLogger("notify_outbox")

# Пауза перед повтором по номеру неудачной попытки (1-я, 2-я, ...): 1→5→15→60 мин.
BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 900, 3600)
MAX_ATTEMPTS = int(os.getenv("NOTIFY_MAX_ATTEMPTS", "8"))
BATCH_LIMIT = 50
RETENTION_DAYS = 30
SUBJECT_PREFIX = os.getenv("NOTIFY_SUBJECT_PREFIX", "[CentralDSP]")

Sender = Callable[["SmtpConfig", EmailMessage], Awaitable[None]]


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    starttls: bool
    ssl: bool
    timeout: float

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)


def smtp_config() -> SmtpConfig:
    """Конфигурация из env. SMTP_SSL=1 — implicit TLS (465), иначе STARTTLS по
    умолчанию включён (SMTP_STARTTLS=0 — plaintext, только для локального relay)."""
    return SmtpConfig(
        host=os.getenv("SMTP_HOST", "").strip(),
        port=int(os.getenv("SMTP_PORT", "587")),
        user=os.getenv("SMTP_USER", ""),
        password=os.getenv("SMTP_PASSWORD", ""),
        sender=os.getenv("SMTP_FROM", "").strip(),
        starttls=os.getenv("SMTP_STARTTLS", "1") not in ("0", "false", "no"),
        ssl=os.getenv("SMTP_SSL", "0") in ("1", "true", "yes"),
        timeout=float(os.getenv("SMTP_TIMEOUT", "20")),
    )


def is_configured() -> bool:
    return smtp_config().configured


def backoff_for(attempt: int) -> int:
    """Секунды до следующей попытки после `attempt`-й неудачи (1-based)."""
    idx = max(0, min(attempt, len(BACKOFF_SECONDS)) - 1)
    return BACKOFF_SECONDS[idx]


def build_message(cfg: SmtpConfig, row: Any) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = row["recipient"]
    msg["Subject"] = f"{SUBJECT_PREFIX} {row['subject']}".strip()
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=cfg.sender.split("@")[-1] or None)
    msg["X-CentralDSP-Kind"] = str(row["kind"])
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(row["body_text"])
    if row["body_html"]:
        msg.add_alternative(row["body_html"], subtype="html")
    return msg


def _send_sync(cfg: SmtpConfig, msg: EmailMessage) -> None:
    ctx = ssl.create_default_context()
    if cfg.ssl:
        client: smtplib.SMTP = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout, context=ctx)
    else:
        client = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)
    with client:
        client.ehlo()
        if not cfg.ssl and cfg.starttls:
            client.starttls(context=ctx)
            client.ehlo()
        if cfg.user:
            client.login(cfg.user, cfg.password)
        client.send_message(msg)


async def smtp_send(cfg: SmtpConfig, msg: EmailMessage) -> None:
    await asyncio.to_thread(_send_sync, cfg, msg)


async def enqueue_email(
    conn: Any,
    *,
    recipients: list[str] | tuple[str, ...],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    kind: str,
    dedup_key: str | None = None,
    source: str = "",
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> int:
    """Положить письмо каждому адресату. `conn` — asyncpg Pool или Connection
    (продюсер может писать в своей транзакции). Дубликаты по
    (recipient, dedup_key) молча пропускаются. Возвращает число новых строк."""
    added = 0
    seen: set[str] = set()
    for raw in recipients:
        rcpt = (raw or "").strip().lower()
        if not rcpt or "@" not in rcpt or rcpt in seen:
            continue
        seen.add(rcpt)
        row = await conn.fetchrow(
            """INSERT INTO notification_outbox
                   (channel, recipient, kind, subject, body_text, body_html, dedup_key,
                    source, entity_type, entity_id)
               VALUES ('email', $1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (channel, recipient, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
               RETURNING id""",
            rcpt, kind, subject[:998], body_text, body_html, dedup_key,
            source, entity_type, str(entity_id) if entity_id is not None else None,
        )
        if row is not None:
            added += 1
    return added


async def dispatch(pool: Any, *, limit: int = BATCH_LIMIT, sender: Sender | None = None) -> dict:
    """Один проход отправки. Без SMTP — ничего не трогает (строки ждут).
    Каждая строка обрабатывается в своей транзакции: падение одной не
    откатывает остальные, конкурирующий dispatcher пропустит занятые строки."""
    cfg = smtp_config()
    obs_metrics.set_gauge("notify_smtp_configured", 1.0 if cfg.configured else 0.0)
    result = {"configured": cfg.configured, "sent": 0, "failed": 0, "retry": 0, "skipped": 0}
    if not cfg.configured:
        await refresh_gauges(pool)
        return result
    send = sender or smtp_send
    processed = 0
    while processed < limit:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT id, recipient, kind, subject, body_text, body_html, attempts
                       FROM notification_outbox
                       WHERE status = 'queued' AND channel = 'email' AND next_attempt_at <= now()
                       ORDER BY next_attempt_at, id
                       FOR UPDATE SKIP LOCKED
                       LIMIT 1"""
                )
                if row is None:
                    break
                processed += 1
                attempt = int(row["attempts"]) + 1
                try:
                    await send(cfg, build_message(cfg, row))
                except Exception as e:  # noqa: BLE001 — любая ошибка SMTP = повтор или failed
                    err = f"{type(e).__name__}: {e}"[:1000]
                    if attempt >= MAX_ATTEMPTS:
                        await conn.execute(
                            "UPDATE notification_outbox SET status='failed', attempts=$2, last_error=$3 WHERE id=$1",
                            row["id"], attempt, err,
                        )
                        obs_metrics.inc("notify_email_failed_total")
                        result["failed"] += 1
                        log.error("outbox %s -> %s failed permanently after %d attempts: %s",
                                  row["id"], row["recipient"], attempt, err)
                    else:
                        delay = backoff_for(attempt)
                        await conn.execute(
                            """UPDATE notification_outbox
                               SET attempts=$2, last_error=$3,
                                   next_attempt_at = now() + make_interval(secs => $4)
                               WHERE id=$1""",
                            row["id"], attempt, err, float(delay),
                        )
                        obs_metrics.inc("notify_email_retry_total")
                        result["retry"] += 1
                        log.warning("outbox %s -> %s attempt %d failed, retry in %ds: %s",
                                    row["id"], row["recipient"], attempt, delay, err)
                else:
                    await conn.execute(
                        "UPDATE notification_outbox SET status='sent', attempts=$2, sent_at=now(), last_error=NULL WHERE id=$1",
                        row["id"], attempt,
                    )
                    obs_metrics.inc("notify_email_sent_total")
                    result["sent"] += 1
    await refresh_gauges(pool)
    return result


async def refresh_gauges(pool: Any) -> None:
    row = await pool.fetchrow(
        """SELECT count(*) AS queued,
                  coalesce(extract(epoch FROM now() - min(created_at)), 0) AS oldest
           FROM notification_outbox WHERE status = 'queued'"""
    )
    obs_metrics.set_gauge("notify_outbox_queued", float(row["queued"]))
    obs_metrics.set_gauge("notify_outbox_oldest_age_seconds", float(row["oldest"]))


async def purge_old(pool: Any, *, days: int = RETENTION_DAYS) -> int:
    """Retention: отправленные/провалившиеся старше `days` — удалить.
    queued не трогаем: их либо отправит dispatcher, либо переведёт в failed."""
    res = await pool.execute(
        "DELETE FROM notification_outbox WHERE status <> 'queued' AND created_at < now() - make_interval(days => $1)",
        int(days),
    )
    try:
        return int(res.split()[-1])
    except (ValueError, IndexError):
        return 0
