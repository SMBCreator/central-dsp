-- 015: domain-level stats for one-click blacklisting from analytics.
-- Written on win/impression/click path from bid_events.domain.
CREATE TABLE IF NOT EXISTS domain_stats_hourly (
    ts_hour           TIMESTAMPTZ NOT NULL,
    campaign_id       UUID,
    supply_partner_id UUID,
    domain            TEXT NOT NULL DEFAULT '',
    wins              BIGINT NOT NULL DEFAULT 0,
    impressions       BIGINT NOT NULL DEFAULT 0,
    clicks            BIGINT NOT NULL DEFAULT 0,
    spend             NUMERIC(14,6) NOT NULL DEFAULT 0,
    UNIQUE (ts_hour, campaign_id, supply_partner_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_domain_stats_ts ON domain_stats_hourly(ts_hour DESC);
CREATE INDEX IF NOT EXISTS idx_domain_stats_domain ON domain_stats_hourly(domain, ts_hour DESC);
