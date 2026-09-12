-- Audit C4b / N-01: единое ядро доставки уведомлений (email сейчас, telegram —
-- следующим шагом на той же таблице). Продюсер (Alertmanager webhook,
-- rules_engine, бизнес-проверки) только INSERT'ит строку — HTTP-запрос
-- никогда не ждёт SMTP. Отправляет задача scheduler `notify_dispatch`
-- (/cron/notify-dispatch, раз в минуту): SKIP LOCKED, backoff, после
-- NOTIFY_MAX_ATTEMPTS попыток -> failed. Retention 30 дней в /cron/retention.
CREATE TABLE IF NOT EXISTS notification_outbox (
    id               bigserial PRIMARY KEY,
    channel          text        NOT NULL CHECK (channel IN ('email', 'telegram')),
    recipient        text        NOT NULL,                          -- email или chat_id
    kind             text        NOT NULL,                          -- tech_alert | autorule_fired | budget_exhausted | ...
    subject          text        NOT NULL,
    body_text        text        NOT NULL,
    body_html        text,
    -- Один и тот же смысл одному адресату — одно письмо (kind+entity+окно);
    -- NULL = без дедупликации.
    dedup_key        text,
    status           text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed')),
    attempts         integer     NOT NULL DEFAULT 0,
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    last_error       text,
    -- Кто/что породило: для страницы «Алерты» и для расследований.
    source           text        NOT NULL DEFAULT '',
    entity_type      text,
    entity_id        text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    sent_at          timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_notification_outbox_dedup
    ON notification_outbox (channel, recipient, dedup_key)
    WHERE dedup_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_notification_outbox_due
    ON notification_outbox (next_attempt_at)
    WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_notification_outbox_created
    ON notification_outbox (created_at);
