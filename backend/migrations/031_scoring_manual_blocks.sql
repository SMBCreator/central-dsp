-- Ручные блокировки площадок в скоринге + индекс под отчёт по доменам.
-- source: 'auto' — выставлено пересчётом по профилю, 'manual' — оператором.
-- Пересчёт (recompute) трогает только source='auto'; ручные блоки живут до снятия.

ALTER TABLE scoring_blocklist
    ADD COLUMN IF NOT EXISTS source      TEXT NOT NULL DEFAULT 'auto',
    ADD COLUMN IF NOT EXISTS created_by  UUID,
    ADD COLUMN IF NOT EXISTS note        TEXT;

ALTER TABLE scoring_blocklist
    DROP CONSTRAINT IF EXISTS scoring_blocklist_source_chk;
ALTER TABLE scoring_blocklist
    ADD CONSTRAINT scoring_blocklist_source_chk CHECK (source IN ('auto', 'manual'));

-- Отчёт агрегирует bid_events по домену за окно профиля: покрывающий индекс
-- под фильтр по кампании + окну и группировку по домену.
CREATE INDEX IF NOT EXISTS idx_bid_events_campaign_created_domain
    ON bid_events (campaign_id, created_at DESC, domain)
    WHERE domain IS NOT NULL;
