-- B13b (audit L-03): акты (разаллокация) в ОРД.
-- Акт = один договор + произвольный период + сумма; строки акта — статистика
-- по (креатив, версия erid, календарный месяц): ОРД принимает статистику
-- строго в рамках месяца, поэтому акт 15.09–15.10 даёт две строки на креатив.
-- externalStatisticId строки = <creative>:v<n>:<YYYY-MM> — тот же, что у месячной
-- выгрузки крона: отправка акта = коррекция той же строки, двойного учёта нет.
-- Следствие: один (креатив, версия, месяц) может входить только в один акт.

CREATE TABLE IF NOT EXISTS ord_invoices (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- владелец = владелец договора
    contract_id      UUID NOT NULL REFERENCES ord_contracts(id) ON DELETE RESTRICT,
    number           TEXT NOT NULL,
    invoice_date     DATE NOT NULL,
    period_start     DATE NOT NULL,
    period_end       DATE NOT NULL,
    amount           NUMERIC(14,2) NOT NULL DEFAULT 0,       -- сумма акта, цена клиента
    is_vat           BOOLEAN NOT NULL DEFAULT true,
    client_role      TEXT NOT NULL DEFAULT 'advertiser',     -- advertiser | agency | publisher | ors
    contractor_role  TEXT NOT NULL DEFAULT 'ors',
    campaign_ids     UUID[] NOT NULL DEFAULT '{}',           -- фильтр предпросмотра (пусто = все кампании договора)
    status           TEXT NOT NULL DEFAULT 'draft',          -- draft | submitting | ok | partial | failed
    ord_provider     TEXT,
    ord_external_id  TEXT,                                   -- externalInvoiceId = id
    error            TEXT,
    submitted_at     TIMESTAMPTZ,
    created_by       UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ord_invoices_period_chk CHECK (period_end >= period_start),
    CONSTRAINT ord_invoices_status_chk CHECK (status IN ('draft', 'submitting', 'ok', 'partial', 'failed')),
    CONSTRAINT ord_invoices_roles_chk CHECK (
        client_role IN ('advertiser', 'agency', 'publisher', 'ors')
        AND contractor_role IN ('advertiser', 'agency', 'publisher', 'ors')
    ),
    CONSTRAINT ord_invoices_number_uq UNIQUE (contract_id, number)
);
CREATE INDEX IF NOT EXISTS idx_ord_invoices_owner ON ord_invoices(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ord_invoice_items (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id             UUID NOT NULL REFERENCES ord_invoices(id) ON DELETE CASCADE,
    creative_id            UUID NOT NULL REFERENCES creatives(id) ON DELETE CASCADE,
    version                INT NOT NULL,
    erid                   TEXT NOT NULL,
    period                 TEXT NOT NULL,                    -- 'YYYY-MM'
    period_start           DATE NOT NULL,                    -- месяц ∩ период акта
    period_end             DATE NOT NULL,
    payment_type           TEXT NOT NULL DEFAULT 'cpm',      -- cpm | cpc
    fact_impressions       BIGINT NOT NULL DEFAULT 0,        -- факт из stats_hourly на момент сохранения
    fact_clicks            BIGINT NOT NULL DEFAULT 0,
    fact_spend             NUMERIC(14,2) NOT NULL DEFAULT 0, -- цена клиента
    impressions            BIGINT NOT NULL DEFAULT 0,        -- значения акта (редактируемые)
    clicks                 BIGINT NOT NULL DEFAULT 0,
    spend                  NUMERIC(14,2) NOT NULL DEFAULT 0,
    unit_cost              NUMERIC(14,4) NOT NULL DEFAULT 0, -- CPM: за 1000 показов; CPC: за клик
    external_statistic_id  TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | rejected
    error                  TEXT,
    CONSTRAINT ord_invoice_items_status_chk CHECK (status IN ('pending', 'accepted', 'rejected')),
    CONSTRAINT ord_invoice_items_uq UNIQUE (invoice_id, creative_id, version, period)
);
-- Один (креатив, версия, месяц) — в одном акте (см. шапку файла).
CREATE UNIQUE INDEX IF NOT EXISTS ux_ord_invoice_items_stat ON ord_invoice_items(external_statistic_id);
CREATE INDEX IF NOT EXISTS idx_ord_invoice_items_creative ON ord_invoice_items(creative_id, period);
