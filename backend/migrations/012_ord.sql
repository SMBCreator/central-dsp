-- 012_ord.sql — ОРД (маркировка рекламы, ФЗ-347): контрагенты, договоры,
-- регистрация креативов (erid), журнал выгрузок.
-- Аддитивная миграция: существующие таблицы не меняются, кроме creatives.erid.

-- Контрагенты (рекламодатель, агентство, издатель...)
CREATE TABLE IF NOT EXISTS counterparties (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    advertiser_id UUID REFERENCES advertisers(id) ON DELETE SET NULL,
    name         TEXT NOT NULL,
    inn          TEXT NOT NULL,                    -- ИНН (10/12 цифр) или номер иностр. рег.
    legal_type   TEXT NOT NULL DEFAULT 'ul',       -- ul | ip | fl | foreign
    role         TEXT NOT NULL DEFAULT 'advertiser', -- advertiser | agency | publisher | ors
    ord_external_id TEXT,                          -- id контрагента в ОРД Ozon
    ord_status   TEXT NOT NULL DEFAULT 'pending',  -- pending | registered | failed
    ord_error    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, inn, role)
);
CREATE INDEX IF NOT EXISTS idx_counterparties_user ON counterparties(user_id);

-- Договоры (изначальный / доходный / посреднический)
CREATE TABLE IF NOT EXISTS ord_contracts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id     UUID NOT NULL REFERENCES counterparties(id) ON DELETE RESTRICT,
    contractor_id UUID NOT NULL REFERENCES counterparties(id) ON DELETE RESTRICT,
    contract_type TEXT NOT NULL DEFAULT 'service',  -- service | mediation | additional
    subject_type  TEXT NOT NULL DEFAULT 'distribution', -- distribution | org_distribution | mediation
    number        TEXT NOT NULL,
    signed_at     DATE NOT NULL,
    amount        NUMERIC(18,6),
    is_vat        BOOLEAN NOT NULL DEFAULT true,
    parent_contract_id UUID REFERENCES ord_contracts(id) ON DELETE SET NULL,
    ord_external_id TEXT,
    ord_status    TEXT NOT NULL DEFAULT 'pending',
    ord_error     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ord_contracts_user ON ord_contracts(user_id);

-- Регистрация креативов в ОРД → erid
CREATE TABLE IF NOT EXISTS ord_creatives (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creative_id   UUID NOT NULL REFERENCES creatives(id) ON DELETE CASCADE,
    contract_id   UUID REFERENCES ord_contracts(id) ON DELETE SET NULL,
    erid          TEXT,                             -- токен маркировки от ОРД
    ord_provider  TEXT NOT NULL DEFAULT 'ozon',
    ord_external_id TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | registered | failed
    payload       JSONB,                            -- что отправили в ОРД
    error         TEXT,
    registered_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (creative_id)
);
CREATE INDEX IF NOT EXISTS idx_ord_creatives_status ON ord_creatives(status);

-- Денормализованный erid на креативе — читается на горячем пути биддера
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS erid TEXT;

-- Журнал месячных выгрузок (акты/статистика показов)
CREATE TABLE IF NOT EXISTS ord_export_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period       TEXT NOT NULL,                     -- 'YYYY-MM'
    kind         TEXT NOT NULL DEFAULT 'stats',     -- stats | acts
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | failed
    rows_sent    INT NOT NULL DEFAULT 0,
    request      JSONB,
    response     JSONB,
    error        TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ord_export_period ON ord_export_log(period, kind);
