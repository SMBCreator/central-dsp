-- Справочник «домен → тематика IAB» (ТЗ контекстного таргетинга, Фаза 3).
-- Нужен, когда SSP не прислала категории: биддер подставляет тематику по домену.
-- Наполняется вручную (source=manual, обучением не перезаписывается) и из трафика
-- (source=learned, /cron/domcat-learn). Аддитивно: существующие таблицы не меняются.

CREATE TABLE IF NOT EXISTS domain_categories (
    domain      TEXT PRIMARY KEY,                       -- нижний регистр, без www.
    iab_codes   TEXT[] NOT NULL DEFAULT '{}',
    source      TEXT NOT NULL DEFAULT 'learned' CHECK (source IN ('manual', 'learned')),
    hits        INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS domain_categories_source_hits
    ON domain_categories (source, hits DESC);
