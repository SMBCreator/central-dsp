"""Бизнес-уведомления (audit C4b / N-03): события кампании/рекламодателя ->
in-app владельцу + письмо адресатам сущности.

Адресаты (`resolve_recipients`): campaign.notify_emails -> advertiser.notify_emails
-> email владельца; первый непустой уровень. `notify_inherit=false` на кампании
запрещает подъём: пустой список = писем не слать (in-app идёт всегда).

Дедуп (`emit`): одна строка `business_alert_log(dedup_key)` на событие; вставка
ON CONFLICT DO NOTHING гейтит и in-app, и письмо. Ключ = kind + сущность + окно
(день/час), поэтому повторные проходы cron не шумят.

События:
  autorule_fired            — из rules_engine после срабатывания правила (ключ: правило+кампания+час);
  budget_exhausted          — daily (spent_today >= daily_budget, ключ по дню) / total (по кампании);
  campaign_stopped_by_system— campaigns.stop_reason проставлен биллингом/правилом (ключ по stopped_at);
  campaign_ending_soon      — end_date завтра или сегодня, кампания активна (ключ по end_date);
  campaign_ended            — end_date прошла, кампания ещё active (ключ по end_date);
  campaign_no_impressions   — активна >= NOTIFY_NO_IMPS_HOURS, есть бюджет/баланс, 0 показов
                              за окно (ключ по дню — одно письмо в сутки);
  low_balance               — баланс < NOTIFY_LOW_BALANCE_DAYS x средний дневной расход
                              за 7 дней, есть активные кампании (ключ по дню).
Проверки гоняет cron `notify_business_checks` (раз в 15 мин), см. `run_checks`.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import notify_outbox
import obs_metrics

log = logging.getLogger("notify_business")

NO_IMPS_HOURS = max(1, int(os.getenv("NOTIFY_NO_IMPS_HOURS", "3")))
LOW_BALANCE_DAYS = max(0.1, float(os.getenv("NOTIFY_LOW_BALANCE_DAYS", "1")))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAILS = 20
STOP_REASONS = {
    "balance": "закончился баланс рекламодателя",
    "total_budget": "исчерпан общий бюджет",
    "autorule": "сработало автоправило",
    "moderation": "решение модерации",
    "admin": "остановлена администратором",
}
KIND_TITLES = {
    "autorule_fired": "Сработало автоправило",
    "budget_exhausted": "Бюджет исчерпан",
    "campaign_stopped_by_system": "Кампания остановлена системой",
    "campaign_ending_soon": "Кампания скоро завершится",
    "campaign_ended": "Кампания завершилась",
    "campaign_no_impressions": "Кампания без показов",
    "low_balance": "Низкий баланс",
}


def normalise_emails(raw: Any) -> list[str]:
    """Список адресов из API: strip/lower/уникальные; невалидный -> ValueError."""
    out: list[str] = []
    for e in raw or []:
        addr = str(e).strip().lower()
        if not addr:
            continue
        if not EMAIL_RE.match(addr):
            raise ValueError(f"Некорректный email: {addr}")
        if addr not in out:
            out.append(addr)
    if len(out) > MAX_EMAILS:
        raise ValueError(f"Не больше {MAX_EMAILS} адресов")
    return out


# ---- recipients -------------------------------------------------------------------

async def resolve_recipients(pool: Any, *, campaign_id: Any = None, advertiser_id: Any = None) -> tuple[list[str], str]:
    """(адреса, источник) — источник: campaign | advertiser | owner | none."""
    adv_id = advertiser_id
    owner_id = None
    if campaign_id is not None:
        camp = await pool.fetchrow(
            "SELECT advertiser_id, user_id, notify_emails, notify_inherit FROM campaigns WHERE id = $1", campaign_id
        )
        if camp is None:
            return [], "none"
        if camp["notify_emails"]:
            return list(camp["notify_emails"]), "campaign"
        if not camp["notify_inherit"]:
            return [], "none"
        adv_id, owner_id = camp["advertiser_id"], camp["user_id"]
    if adv_id is not None:
        adv = await pool.fetchrow("SELECT user_id, notify_emails FROM advertisers WHERE id = $1", adv_id)
        if adv is None:
            return [], "none"
        if adv["notify_emails"]:
            return list(adv["notify_emails"]), "advertiser"
        owner_id = adv["user_id"]
    if owner_id is None:
        return [], "none"
    email = await pool.fetchval("SELECT email FROM users WHERE id = $1 AND coalesce(status, 'active') = 'active'", owner_id)
    email = (email or "").strip().lower()
    return ([email], "owner") if email and EMAIL_RE.match(email) else ([], "none")


# ---- emit -------------------------------------------------------------------------------

def _money(v: Any) -> str:
    try:
        return f"{float(v):,.2f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def _render(kind: str, title: str, lines: list[str], link: str | None) -> tuple[str, str, str]:
    subject = f"{KIND_TITLES.get(kind, kind)}: {title}"[:200]
    text = "\n".join([KIND_TITLES.get(kind, kind), title, "", *lines]).strip() + "\n"
    html_body = (
        f"<p style='font:14px system-ui'><strong>{html.escape(KIND_TITLES.get(kind, kind))}</strong><br>"
        f"{html.escape(title)}</p>"
        f"<p style='font:13px system-ui;color:#374151'>{'<br>'.join(html.escape(l) for l in lines)}</p>"
    )
    return subject, text, html_body


async def emit(
    pool: Any,
    kind: str,
    *,
    dedup_key: str,
    entity_type: str,
    entity_id: Any,
    owner_user_id: Any,
    title: str,
    lines: list[str],
    link: str | None = None,
    campaign_id: Any = None,
    advertiser_id: Any = None,
    payload: dict | None = None,
) -> bool:
    """Одно событие: гейт в business_alert_log, in-app владельцу, письмо адресатам.
    True — событие новое и разослано; False — уже было (дедуп)."""
    recipients, source = await resolve_recipients(pool, campaign_id=campaign_id, advertiser_id=advertiser_id)
    row = await pool.fetchrow(
        """INSERT INTO business_alert_log (dedup_key, kind, entity_type, entity_id, recipients, payload)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb)
           ON CONFLICT (dedup_key) DO NOTHING RETURNING dedup_key""",
        dedup_key, kind, entity_type, entity_id, recipients, json.dumps(payload or {}, default=str),
    )
    if row is None:
        obs_metrics.inc("notify_business_deduplicated_total")
        return False
    obs_metrics.inc("notify_business_events_total")
    subject, text, html_body = _render(kind, title, lines, link)
    if owner_user_id is not None:
        await pool.execute(
            "INSERT INTO notifications (user_id, type, title, body, link) VALUES ($1, $2, $3, $4, $5)",
            owner_user_id, kind, subject[:200], "\n".join(lines)[:2000], link,
        )
    if recipients:
        await notify_outbox.enqueue_email(
            pool, recipients=recipients, subject=subject, body_text=text, body_html=html_body,
            kind=kind, source=f"business:{source}", dedup_key=dedup_key,
            entity_type=entity_type, entity_id=str(entity_id),
        )
    return True


# ---- event helpers -------------------------------------------------------------------

def campaign_link(campaign_id: Any) -> str:
    return f"/campaigns/{campaign_id}"


async def autorule_fired(pool: Any, *, rule: dict, campaign_id: Any, value: float, message: str) -> bool:
    """Из rules_engine: одно письмо на правило+кампанию в час (правило может
    срабатывать каждый прогон, пока метрика за порогом)."""
    camp = await pool.fetchrow("SELECT name, user_id FROM campaigns WHERE id = $1", campaign_id)
    if camp is None:
        return False
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    return await emit(
        pool, "autorule_fired",
        dedup_key=f"autorule_fired:{rule['id']}:{campaign_id}:{hour}",
        entity_type="campaign", entity_id=campaign_id, owner_user_id=camp["user_id"],
        campaign_id=campaign_id, title=f"«{camp['name']}» — правило «{rule['name']}»",
        lines=[
            f"Метрика {rule['metric']} = {round(float(value), 4)} (порог {rule['operator']} {rule['threshold']}"
            f" за {rule['window_hours']} ч)",
            f"Действие: {message}",
        ],
        link=campaign_link(campaign_id),
        payload={"rule_id": str(rule["id"]), "metric": rule["metric"], "value": value, "action": rule["action"]},
    )


# ---- periodic checks ---------------------------------------------------------------------

async def _check_budget_exhausted(pool: Any, today: date) -> int:
    n = 0
    rows = await pool.fetch(
        """SELECT id, user_id, name, daily_budget, spent_today, total_budget, spent_total
           FROM campaigns
           WHERE (status = 'active' AND daily_budget > 0 AND spent_today_date = $1 AND spent_today >= daily_budget)
              OR (status IN ('active', 'paused') AND total_budget > 0 AND spent_total >= total_budget)""",
        today,
    )
    for c in rows:
        if float(c["total_budget"] or 0) > 0 and float(c["spent_total"] or 0) >= float(c["total_budget"]):
            n += await emit(
                pool, "budget_exhausted", dedup_key=f"budget_exhausted:total:{c['id']}",
                entity_type="campaign", entity_id=c["id"], owner_user_id=c["user_id"], campaign_id=c["id"],
                title=f"«{c['name']}» — общий бюджет",
                lines=[f"Потрачено {_money(c['spent_total'])} из {_money(c['total_budget'])}. Кампания остановлена.",
                       "Увеличьте общий бюджет, чтобы продолжить показы."],
                link=campaign_link(c["id"]), payload={"scope": "total"},
            )
        else:
            n += await emit(
                pool, "budget_exhausted", dedup_key=f"budget_exhausted:daily:{c['id']}:{today.isoformat()}",
                entity_type="campaign", entity_id=c["id"], owner_user_id=c["user_id"], campaign_id=c["id"],
                title=f"«{c['name']}» — дневной бюджет",
                lines=[f"Потрачено {_money(c['spent_today'])} из {_money(c['daily_budget'])} за сегодня.",
                       "Показы возобновятся завтра или после увеличения дневного бюджета."],
                link=campaign_link(c["id"]), payload={"scope": "daily", "day": today.isoformat()},
            )
    return n


async def _check_stopped_by_system(pool: Any) -> int:
    n = 0
    rows = await pool.fetch(
        """SELECT id, user_id, name, stop_reason, stopped_at FROM campaigns
           WHERE status = 'paused' AND stop_reason IS NOT NULL AND stopped_at > now() - interval '7 days'"""
    )
    for c in rows:
        stamp = c["stopped_at"].strftime("%Y%m%d%H%M%S") if c["stopped_at"] else "na"
        reason = STOP_REASONS.get(c["stop_reason"], c["stop_reason"])
        n += await emit(
            pool, "campaign_stopped_by_system", dedup_key=f"campaign_stopped_by_system:{c['id']}:{stamp}",
            entity_type="campaign", entity_id=c["id"], owner_user_id=c["user_id"], campaign_id=c["id"],
            title=f"«{c['name']}»",
            lines=[f"Причина: {reason}.", "Кампания в статусе «пауза»; после устранения причины запустите её снова."],
            link=campaign_link(c["id"]), payload={"stop_reason": c["stop_reason"]},
        )
    return n


async def _check_dates(pool: Any, today: date) -> int:
    n = 0
    rows = await pool.fetch(
        "SELECT id, user_id, name, end_date FROM campaigns WHERE status = 'active' AND end_date IS NOT NULL AND end_date <= $1",
        today + timedelta(days=1),
    )
    for c in rows:
        end = c["end_date"]
        if end < today:
            n += await emit(
                pool, "campaign_ended", dedup_key=f"campaign_ended:{c['id']}:{end.isoformat()}",
                entity_type="campaign", entity_id=c["id"], owner_user_id=c["user_id"], campaign_id=c["id"],
                title=f"«{c['name']}»",
                lines=[f"Дата окончания {end.isoformat()} прошла, показы прекращены.",
                       "Продлите период в настройках кампании, если показы нужно продолжить."],
                link=campaign_link(c["id"]), payload={"end_date": end.isoformat()},
            )
        else:
            n += await emit(
                pool, "campaign_ending_soon", dedup_key=f"campaign_ending_soon:{c['id']}:{end.isoformat()}",
                entity_type="campaign", entity_id=c["id"], owner_user_id=c["user_id"], campaign_id=c["id"],
                title=f"«{c['name']}»",
                lines=[f"Кампания завершится {end.isoformat()} (меньше чем через сутки).",
                       "Продлите период, если показы нужно продолжить."],
                link=campaign_link(c["id"]), payload={"end_date": end.isoformat()},
            )
    return n


async def _check_no_impressions(pool: Any, today: date) -> int:
    n = 0
    rows = await pool.fetch(
        f"""SELECT c.id, c.user_id, c.name
            FROM campaigns c JOIN advertisers a ON a.id = c.advertiser_id
            WHERE c.status = 'active' AND a.status = 'active' AND a.balance > 0
              AND (c.start_date IS NULL OR c.start_date <= $1)
              AND (c.end_date IS NULL OR c.end_date >= $1)
              AND (c.daily_budget <= 0 OR c.spent_today_date IS DISTINCT FROM $1 OR c.spent_today < c.daily_budget)
              AND (c.total_budget <= 0 OR c.spent_total < c.total_budget)
              AND c.updated_at <= now() - interval '{NO_IMPS_HOURS} hours'
              AND NOT EXISTS (
                    SELECT 1 FROM stats_hourly s
                    WHERE s.campaign_id = c.id AND s.ts_hour >= now() - interval '{NO_IMPS_HOURS} hours'
                      AND s.impressions > 0)""",
        today,
    )
    for c in rows:
        n += await emit(
            pool, "campaign_no_impressions", dedup_key=f"campaign_no_impressions:{c['id']}:{today.isoformat()}",
            entity_type="campaign", entity_id=c["id"], owner_user_id=c["user_id"], campaign_id=c["id"],
            title=f"«{c['name']}»",
            lines=[f"Кампания активна, бюджет и баланс есть, но за последние {NO_IMPS_HOURS} ч нет ни одного показа.",
                   "Проверьте диагностику кампании: ставки, креативы, таргетинг, маркировку ОРД."],
            link=campaign_link(c["id"]), payload={"hours": NO_IMPS_HOURS},
        )
    return n


async def _check_low_balance(pool: Any, today: date) -> int:
    n = 0
    rows = await pool.fetch(
        """SELECT a.id, a.user_id, a.name, a.balance, a.currency,
                  coalesce((SELECT sum(s.spend) FROM stats_hourly s JOIN campaigns c2 ON c2.id = s.campaign_id
                            WHERE c2.advertiser_id = a.id AND s.ts_hour >= now() - interval '7 days'), 0) / 7.0 AS avg_daily
           FROM advertisers a
           WHERE a.status = 'active' AND a.balance > 0
             AND EXISTS (SELECT 1 FROM campaigns c WHERE c.advertiser_id = a.id AND c.status = 'active')"""
    )
    for a in rows:
        avg_daily = float(a["avg_daily"] or 0)
        if avg_daily <= 0 or float(a["balance"]) >= LOW_BALANCE_DAYS * avg_daily:
            continue
        days_left = float(a["balance"]) / avg_daily
        n += await emit(
            pool, "low_balance", dedup_key=f"low_balance:{a['id']}:{today.isoformat()}",
            entity_type="advertiser", entity_id=a["id"], owner_user_id=a["user_id"], advertiser_id=a["id"],
            title=f"«{a['name']}»",
            lines=[f"Баланс {_money(a['balance'])} {a['currency']}, средний расход {_money(avg_daily)} {a['currency']}/день"
                   f" — хватит примерно на {days_left:.1f} дн.",
                   "Пополните баланс, иначе кампании остановятся автоматически."],
            link=f"/advertisers/{a['id']}", payload={"balance": float(a["balance"]), "avg_daily": avg_daily},
        )
    return n


async def run_checks(pool: Any, today: date | None = None) -> dict[str, int]:
    """Один проход всех периодических проверок. Ошибка одной проверки не
    останавливает остальные (считается в notify_business_check_errors_total)."""
    today = today or datetime.now(timezone.utc).date()
    result: dict[str, int] = {}
    checks = (
        ("budget_exhausted", lambda: _check_budget_exhausted(pool, today)),
        ("campaign_stopped_by_system", lambda: _check_stopped_by_system(pool)),
        ("dates", lambda: _check_dates(pool, today)),
        ("campaign_no_impressions", lambda: _check_no_impressions(pool, today)),
        ("low_balance", lambda: _check_low_balance(pool, today)),
    )
    for name, fn in checks:
        try:
            result[name] = int(await fn())
        except Exception:  # noqa: BLE001 — одна сломанная проверка не должна глушить остальные
            log.exception("notify_business check %s failed", name)
            obs_metrics.inc("notify_business_check_errors_total")
            result[name] = -1
    return result
