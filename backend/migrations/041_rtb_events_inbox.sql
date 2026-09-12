-- Audit E-03: inbox для consumer-а Redis Stream rtb:events.
--
-- Проблема: at-least-once доставка (XACK после COMMIT) означает, что при падении
-- между COMMIT и XACK батч перечитывается и bid_events/stats_hourly.bids
-- задваиваются. bid_events партиционирована по created_at (PK (id, created_at)),
-- поэтому UNIQUE по stream id там неудобен — заводим отдельный inbox.
--
-- Правило: INSERT INTO rtb_events_inbox ... ON CONFLICT DO NOTHING RETURNING
-- в той же транзакции, что и bid_events/stats_hourly; агрегаты считаются только
-- по реально вставленным stream_id. Ретенция — GET /cron/retention (3 дня; стрим
-- на стороне Go ограничен MAXLEN ~200k, то есть минутами).
CREATE TABLE IF NOT EXISTS rtb_events_inbox (
    stream_id   text PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rtb_events_inbox_created ON rtb_events_inbox (created_at);
