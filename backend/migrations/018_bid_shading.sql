-- 018: bid shading (first-price optimization)
-- Factors per (campaign, supply partner), recomputed hourly by cron.
-- Opt-in per campaign: campaigns.bid_shading defaults to FALSE.

CREATE TABLE IF NOT EXISTS bid_shading_factors (
    campaign_id       UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    supply_partner_id UUID NOT NULL REFERENCES supply_partners(id) ON DELETE CASCADE,
    factor            NUMERIC(6,4) NOT NULL DEFAULT 1.0,
    bids              INT NOT NULL DEFAULT 0,
    wins              INT NOT NULL DEFAULT 0,
    avg_ratio         NUMERIC(6,4),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, supply_partner_id)
);

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS bid_shading BOOLEAN NOT NULL DEFAULT FALSE;
