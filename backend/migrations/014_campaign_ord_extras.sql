-- 014: Фаза A — трекеры, поля ОРД, ККТУ, глобальный блэклист, настройки, курсы валют

-- 1. Сторонние трекеры на креативе (показ и клик)
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS imp_tracker_url TEXT;
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS click_tracker_url TEXT;

-- 2. Недостающие поля ОРД: местонахождение контрагента
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS address TEXT;

-- 3. Тематика ККТУ на кампании (привязывается к креативам при регистрации в ОРД)
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS kktu_code TEXT;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS kktu_label TEXT;

-- 4. Глобальный блэклист/вайтлист площадок (уровень аккаунта, поверх таргетинга line item)
CREATE TABLE IF NOT EXISTS domain_lists (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    domain     TEXT NOT NULL,
    list_type  TEXT NOT NULL DEFAULT 'black',  -- black | white
    source     TEXT NOT NULL DEFAULT 'manual', -- manual | stats_click | rule
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, domain, list_type)
);
CREATE INDEX IF NOT EXISTS idx_domain_lists_user ON domain_lists(user_id, list_type);

-- 5. Настройки аккаунта (key-value, JSONB значения)
CREATE TABLE IF NOT EXISTS app_settings (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

-- 6. Курсы валют (ЦБ РФ, ежедневно; base = RUB)
CREATE TABLE IF NOT EXISTS fx_rates (
    currency   TEXT NOT NULL,        -- USD | EUR | ...
    rate_date  DATE NOT NULL,
    rate       NUMERIC(18,6) NOT NULL, -- рублей за единицу валюты
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (currency, rate_date)
);
