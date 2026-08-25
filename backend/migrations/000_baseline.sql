-- 000_baseline: full pre-007 schema reconstructed from application code.
--
-- WHY THIS EXISTS: migrations 001-006 were lost; the repo only carried 007+.
-- This file recreates every table the current backend reads/writes so that a
-- FRESH database (dev, staging, CI, bid-service) can be built from scratch.
--
-- FOR THE EXISTING PRODUCTION DATABASE: do NOT execute this file. Run
--   python backend/migrate.py --stamp-all
-- once to mark all migrations as applied (see backend/migrate.py).
--
-- Every statement is idempotent (IF NOT EXISTS) so an accidental run against
-- an existing database is a no-op rather than a disaster.

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid on PG < 13

-- Users & sessions (session-cookie auth, bcrypt hashes)
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'manager',  -- admin | manager | viewer
    status        TEXT NOT NULL DEFAULT 'active',   -- active | blocked
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token      TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- Advertisers (client accounts holding money)
CREATE TABLE IF NOT EXISTS advertisers (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    domain     TEXT,
    category   TEXT,
    balance    NUMERIC(14,4) NOT NULL DEFAULT 0,
    currency   TEXT NOT NULL DEFAULT 'USD',
    status     TEXT NOT NULL DEFAULT 'active',      -- active | paused | blocked
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_advertisers_user ON advertisers(user_id);

-- Campaigns
CREATE TABLE IF NOT EXISTS campaigns (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    advertiser_id             UUID NOT NULL REFERENCES advertisers(id) ON DELETE CASCADE,
    user_id                   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                      TEXT NOT NULL,
    objective                 TEXT,                 -- awareness | traffic | conversions
    status                    TEXT NOT NULL DEFAULT 'paused', -- active | paused | archived
    daily_budget              NUMERIC(12,4) NOT NULL DEFAULT 0,
    total_budget              NUMERIC(12,4) NOT NULL DEFAULT 0,
    spent_today               NUMERIC(14,6) NOT NULL DEFAULT 0,
    spent_total               NUMERIC(14,6) NOT NULL DEFAULT 0,
    start_date                DATE,
    end_date                  DATE,
    pacing                    TEXT NOT NULL DEFAULT 'asap',   -- asap | even
    frequency_cap_impressions INT,
    frequency_cap_period_hours INT,
    auto_optimize             BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_campaigns_user ON campaigns(user_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_advertiser ON campaigns(advertiser_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status) WHERE status = 'active';

-- Line items (bid price + targeting)
CREATE TABLE IF NOT EXISTS line_items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id  UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    bid_type     TEXT NOT NULL DEFAULT 'cpm',
    bid_price    NUMERIC(12,4) NOT NULL DEFAULT 0,
    max_bid      NUMERIC(12,4),
    daily_budget NUMERIC(12,4) NOT NULL DEFAULT 0,
    spent_today  NUMERIC(14,6) NOT NULL DEFAULT 0,
    targeting    JSONB NOT NULL DEFAULT '{}'::jsonb,
    status       TEXT NOT NULL DEFAULT 'active',    -- active | paused
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_line_items_campaign ON line_items(campaign_id);
CREATE INDEX IF NOT EXISTS idx_line_items_status ON line_items(status) WHERE status = 'active';

-- Creatives (banner / native / video; push subtype/crop arrive in 009)
CREATE TABLE IF NOT EXISTS creatives (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id        UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    line_item_id       UUID REFERENCES line_items(id) ON DELETE SET NULL,
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name               TEXT NOT NULL,
    type               TEXT NOT NULL,               -- banner | native | video | push
    width              INT,
    height             INT,
    asset_url          TEXT,
    click_url          TEXT,
    html_snippet       TEXT,
    native_title       TEXT,
    native_description TEXT,
    native_cta         TEXT,
    native_icon_url    TEXT,
    video_url          TEXT,
    video_duration     INT,
    vast_xml           TEXT,
    status             TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_creatives_campaign ON creatives(campaign_id);

-- Supply partners (SSP endpoints)
CREATE TABLE IF NOT EXISTS supply_partners (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    endpoint_key     TEXT NOT NULL UNIQUE,
    auction_type     TEXT NOT NULL DEFAULT 'first_price', -- first_price | second_price
    bid_floor        NUMERIC(12,4) NOT NULL DEFAULT 0,
    qps_limit        INT,
    allowed_ad_types JSONB NOT NULL DEFAULT '["banner","native","video"]'::jsonb,
    notes            TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Bid events (raw RTB event log; os/browser columns arrive in 007/008)
CREATE TABLE IF NOT EXISTS bid_events (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type        TEXT NOT NULL,                -- bid | win | impression | click | video_*
    bid_id            TEXT NOT NULL,
    imp_id            TEXT,
    campaign_id       UUID,
    line_item_id      UUID,
    creative_id       UUID,
    supply_partner_id UUID,
    price             NUMERIC(12,4),
    clear_price       NUMERIC(12,4),
    device_type       TEXT,
    geo_country       TEXT,
    ad_type           TEXT,
    domain            TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bid_events_bid_id ON bid_events(bid_id);
CREATE INDEX IF NOT EXISTS idx_bid_events_created ON bid_events(created_at DESC);

-- Hourly stats rollup (conversions column arrives in 007, video counters in 009)
CREATE TABLE IF NOT EXISTS stats_hourly (
    ts_hour           TIMESTAMPTZ NOT NULL,
    campaign_id       UUID,
    line_item_id      UUID,
    creative_id       UUID,
    supply_partner_id UUID,
    bids              BIGINT NOT NULL DEFAULT 0,
    wins              BIGINT NOT NULL DEFAULT 0,
    impressions       BIGINT NOT NULL DEFAULT 0,
    clicks            BIGINT NOT NULL DEFAULT 0,
    spend             NUMERIC(14,6) NOT NULL DEFAULT 0,
    UNIQUE (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id)
);
CREATE INDEX IF NOT EXISTS idx_stats_hourly_ts ON stats_hourly(ts_hour DESC);
CREATE INDEX IF NOT EXISTS idx_stats_hourly_campaign ON stats_hourly(campaign_id, ts_hour DESC);

-- Billing ledger
CREATE TABLE IF NOT EXISTS transactions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    advertiser_id UUID REFERENCES advertisers(id) ON DELETE SET NULL,
    type          TEXT NOT NULL,                    -- deposit | spend | adjustment
    amount        NUMERIC(14,6) NOT NULL,
    balance_after NUMERIC(14,4),
    description   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_advertiser ON transactions(advertiser_id, created_at DESC);

-- Auto-rules
CREATE TABLE IF NOT EXISTS rules (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    campaign_id       UUID REFERENCES campaigns(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    metric            TEXT NOT NULL,
    operator          TEXT NOT NULL,
    threshold         NUMERIC(14,6) NOT NULL,
    action            TEXT NOT NULL,
    action_value      NUMERIC(14,6),
    window_hours      INT NOT NULL DEFAULT 24,
    status            TEXT NOT NULL DEFAULT 'active',
    last_triggered_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rules_user ON rules(user_id) WHERE status = 'active';

-- White-label client access: share links + guest viewer accounts
CREATE TABLE IF NOT EXISTS share_links (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    advertiser_id  UUID REFERENCES advertisers(id) ON DELETE CASCADE,
    token          TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    brand_name     TEXT,
    brand_color    TEXT,
    brand_logo_url TEXT,
    expires_at     TIMESTAMPTZ,
    revoked        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS guest_access (
    user_id        UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    owner_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    advertiser_id  UUID REFERENCES advertisers(id) ON DELETE CASCADE,
    brand_name     TEXT,
    brand_color    TEXT,
    brand_logo_url TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_guest_access_owner ON guest_access(owner_id);

-- Buy optimizer block lists
CREATE TABLE IF NOT EXISTS optimizer_blocks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    block_type  TEXT NOT NULL,
    block_value TEXT NOT NULL,
    reason      TEXT,
    metrics     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, block_type, block_value)
);
