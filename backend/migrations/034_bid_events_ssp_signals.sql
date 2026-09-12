-- 034: SSP-сигналы в bid_events + события loss от Go-биддера.
--
-- Зачем: Go-биддер (единственный боевой) теперь пишет события «bid» и «loss» в
-- Redis Stream rtb:events, а backend/events_consumer.py переносит их сюда.
-- Новые поля — разрезы для отчётов «по площадкам» и оптимизатора:
--   tagid / publisher_id / bundle — где именно показались (imp.tagid, site|app.publisher.id, app.bundle)
--   geo_region / geo_city         — гео глубже страны (device.geo)
--   bidfloor                      — floor SSP на момент бида (для анализа «сколько не хватило»)
--   loss_reason / min_to_win      — из lurl (${AUCTION_LOSS} / ${AUCTION_MIN_TO_WIN})
--   is_test                       — OpenRTB test=1: событие видно в отчётах, но не в деньгах
--
-- Идемпотентно. Работает и с партиционированной bid_events (033), и с legacy.
-- ADD COLUMN без DEFAULT-выражения на партиционированной таблице — метаданные, без rewrite.

ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS tagid        TEXT;
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS publisher_id TEXT;
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS bundle       TEXT;
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS geo_region   TEXT;
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS geo_city     TEXT;
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS bidfloor     NUMERIC(12,4);
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS loss_reason  TEXT;
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS min_to_win   NUMERIC(12,4);
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS is_test      BOOLEAN NOT NULL DEFAULT FALSE;

-- Отчёт «по площадкам» (tagid) и «почему проигрываем» (loss_reason) читают
-- за окно по кампании; индексы частичные, чтобы не раздувать горячую таблицу.
CREATE INDEX IF NOT EXISTS idx_bid_events_tagid
    ON bid_events (campaign_id, tagid, created_at DESC)
    WHERE tagid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bid_events_loss
    ON bid_events (campaign_id, loss_reason, created_at DESC)
    WHERE event_type = 'loss';

-- Курсор consumer-а: последний обработанный id стрима на случай пересоздания
-- consumer-группы (XGROUP CREATE ... $ потерял бы события, накопленные пока
-- backend лежал). Одна строка.
CREATE TABLE IF NOT EXISTS rtb_events_cursor (
    stream      TEXT PRIMARY KEY,
    last_id     TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
