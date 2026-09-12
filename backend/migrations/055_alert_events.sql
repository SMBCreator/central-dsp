-- Audit C4b / N-02: технические алерты из Alertmanager (webhook /alerts/webhook).
-- Одна строка = один переход (firing или resolved) одного алерта. Уникальность
-- по (fingerprint, status, starts_at) даёт идемпотентность: Alertmanager
-- повторяет firing каждые repeat_interval, но в БД, in-app и email оно попадёт
-- один раз. Таблица — источник страницы «Алерты» в UI суперадмина.
CREATE TABLE IF NOT EXISTS alert_events (
    id           bigserial PRIMARY KEY,
    fingerprint  text        NOT NULL,
    alertname    text        NOT NULL,
    severity     text        NOT NULL DEFAULT 'warning',
    status       text        NOT NULL CHECK (status IN ('firing', 'resolved')),
    starts_at    timestamptz NOT NULL,
    ends_at      timestamptz,
    summary      text        NOT NULL DEFAULT '',
    description  text        NOT NULL DEFAULT '',
    labels       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    annotations  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    external_url text,
    received_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_events_transition
    ON alert_events (fingerprint, status, starts_at);

CREATE INDEX IF NOT EXISTS idx_alert_events_received
    ON alert_events (received_at DESC);
