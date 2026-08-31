-- 027: Поведенческие метрики пикселя — глубина скролла.
-- Скрипт пикселя передаёт максимальный процент прокрутки страницы (0-100)
-- вместе с heartbeat/pagehide; используется в скоринге качества площадок.
ALTER TABLE pixel_events ADD COLUMN IF NOT EXISTS scroll_pct SMALLINT NOT NULL DEFAULT 0;
