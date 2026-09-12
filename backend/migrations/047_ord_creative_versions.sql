-- 047_ord_creative_versions.sql — B13 (audit L-02/L-03): история erid по версиям.
--
-- Было: ord_creatives UNIQUE(creative_id) — перерегистрация делала upsert нового
-- erid поверх старого, история маркировки исчезала, месячная статистика уходила
-- в ОРД под текущим erid за весь период. Стало: одна строка на версию,
-- erid неизменяем; текущая версия — та, у которой superseded_at IS NULL.
-- Статистика показов относится к версии, действовавшей на конец часа
-- (см. routers/cron.py::ord_monthly_export и docs/audit/ord_spec.md).

ALTER TABLE ord_creatives DROP CONSTRAINT IF EXISTS ord_creatives_creative_id_key;
ALTER TABLE ord_creatives ADD COLUMN IF NOT EXISTS version       INT NOT NULL DEFAULT 1;
ALTER TABLE ord_creatives ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

-- Строки, уже помеченные superseded старым кодом, получают момент закрытия.
UPDATE ord_creatives SET superseded_at = updated_at
 WHERE status = 'superseded' AND superseded_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_ord_creatives_creative_version
    ON ord_creatives (creative_id, version);
-- Ровно одна открытая (текущая) версия на креатив.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ord_creatives_current
    ON ord_creatives (creative_id) WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ord_creatives_registered
    ON ord_creatives (creative_id, registered_at DESC) WHERE erid IS NOT NULL;

-- Юридически значимые поля креатива (контрагент, договор, ККТУ) после выдачи
-- erid заморожены: их смена = новый креатив с новым erid и отдельной статистикой.
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS ord_locked_at TIMESTAMPTZ;
UPDATE creatives c SET ord_locked_at = oc.first_registered
  FROM (SELECT creative_id, min(registered_at) AS first_registered
          FROM ord_creatives WHERE erid IS NOT NULL GROUP BY creative_id) oc
 WHERE oc.creative_id = c.id AND c.ord_locked_at IS NULL;

-- Построчный результат выгрузки статистики: что именно принял/отклонил ОРД.
CREATE TABLE IF NOT EXISTS ord_export_rows (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    log_id        UUID NOT NULL REFERENCES ord_export_log(id) ON DELETE CASCADE,
    creative_id   UUID,
    version       INT,
    erid          TEXT,
    external_statistic_id TEXT NOT NULL,
    impressions   BIGINT NOT NULL DEFAULT 0,
    clicks        BIGINT NOT NULL DEFAULT 0,
    spend         NUMERIC(18,6) NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | rejected
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ord_export_rows_log ON ord_export_rows(log_id);
CREATE INDEX IF NOT EXISTS idx_ord_export_rows_creative ON ord_export_rows(creative_id, version);

-- Итоги выгрузки: статус ok | partial | failed | empty (см. ord_spec.md).
ALTER TABLE ord_export_log ADD COLUMN IF NOT EXISTS rows_accepted INT NOT NULL DEFAULT 0;
ALTER TABLE ord_export_log ADD COLUMN IF NOT EXISTS rows_rejected INT NOT NULL DEFAULT 0;
