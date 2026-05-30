-- ─────────────────────────────────────────
-- Central DSP — Initial DB Schema
-- Run automatically on first postgres start
-- ─────────────────────────────────────────

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for text search

-- ─────────────────────────────────────────
-- USERS & AUTH
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name     VARCHAR(255),
    role          VARCHAR(20)  NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin','manager','viewer')),
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- CAMPAIGNS
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id                 SERIAL PRIMARY KEY,
    user_id            INT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name               VARCHAR(255) NOT NULL,
    status             VARCHAR(20)  NOT NULL DEFAULT 'active' CHECK (status IN ('active','stopped','paused','archived')),
    camp_code          VARCHAR(32)  UNIQUE NOT NULL,
    is_ab              BOOLEAN      NOT NULL DEFAULT FALSE,
    ab_limit           INT,
    offer_value        NUMERIC(10,4) DEFAULT 0,
    attribution_model  VARCHAR(20)  NOT NULL DEFAULT 'last_click' CHECK (attribution_model IN ('last_click','first_click','linear')),
    daily_budget       NUMERIC(10,4),
    total_budget       NUMERIC(10,4),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- LINKS (placements / A-B variants)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS links (
    id              SERIAL PRIMARY KEY,
    campaign_id     INT          NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    platform        VARCHAR(100),
    external_id     VARCHAR(255),
    original_url    TEXT         NOT NULL,
    short_code      VARCHAR(32)  UNIQUE NOT NULL,
    custom_slug     VARCHAR(128) UNIQUE,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    cost_per_click  NUMERIC(10,6) DEFAULT 0,
    expires_at      TIMESTAMPTZ,
    geo_rules       JSONB        DEFAULT '{}',
    device_rules    JSONB        DEFAULT '{}',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- PIXELS
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pixels (
    id           SERIAL PRIMARY KEY,
    campaign_id  INT          NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name         VARCHAR(255) NOT NULL,
    pixel_id     UUID         NOT NULL DEFAULT uuid_generate_v4(),
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    last_fire_at TIMESTAMPTZ,
    total_events BIGINT       NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- EVENTS (TimeSeries — main analytics table)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id           BIGSERIAL    PRIMARY KEY,
    pixel_id     UUID         REFERENCES pixels(pixel_id),
    link_id      INT          REFERENCES links(id),
    auction_id   VARCHAR(128),  -- RTB auction ID from SSP
    imp_id       VARCHAR(128),  -- RTB impression ID
    session_id   VARCHAR(64),
    event_name   VARCHAR(64)  NOT NULL,
    sub_id       VARCHAR(255),
    creative_id  VARCHAR(255),
    click_id     VARCHAR(128),
    value        NUMERIC(12,6) DEFAULT 0,
    currency     VARCHAR(8)   DEFAULT 'USD',
    metadata     JSONB        DEFAULT '{}',
    ip           INET,
    country_code VARCHAR(4),
    device_type  VARCHAR(20),
    os           VARCHAR(50),
    browser      VARCHAR(50),
    user_agent   TEXT,
    referer      TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_events_created_at  ON events(created_at DESC);
CREATE INDEX idx_events_link_id     ON events(link_id, created_at DESC);
CREATE INDEX idx_events_pixel_id    ON events(pixel_id, created_at DESC);
CREATE INDEX idx_events_event_name  ON events(event_name, created_at DESC);
CREATE INDEX idx_events_click_id    ON events(click_id);
CREATE INDEX idx_events_sub_id      ON events(sub_id, created_at DESC);

-- ─────────────────────────────────────────
-- CLICKS (TimeSeries — for date-range analytics)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clicks (
    id           BIGSERIAL    PRIMARY KEY,
    link_id      INT          NOT NULL REFERENCES links(id),
    sub_id       VARCHAR(255),
    creative_id  VARCHAR(255),
    session_id   VARCHAR(64),
    click_id     UUID         NOT NULL DEFAULT uuid_generate_v4(),
    ip           INET,
    country_code VARCHAR(4),
    device_type  VARCHAR(20),
    cost         NUMERIC(10,6) DEFAULT 0,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_clicks_created_at ON clicks(created_at DESC);
CREATE INDEX idx_clicks_link_id    ON clicks(link_id, created_at DESC);
CREATE INDEX idx_clicks_click_id   ON clicks(click_id);

-- ─────────────────────────────────────────
-- POSTBACKS (S2S audit log)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS postbacks (
    id         BIGSERIAL    PRIMARY KEY,
    click_id   UUID,
    payout     NUMERIC(10,6) DEFAULT 0,
    currency   VARCHAR(8)   DEFAULT 'USD',
    status     VARCHAR(50),
    raw_params JSONB        DEFAULT '{}',
    processed  BOOLEAN      NOT NULL DEFAULT FALSE,
    ip         INET,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_postbacks_click_id  ON postbacks(click_id);
CREATE INDEX idx_postbacks_created_at ON postbacks(created_at DESC);

-- ─────────────────────────────────────────
-- RTB — CREATIVES
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS creatives (
    id           SERIAL PRIMARY KEY,
    campaign_id  INT          NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name         VARCHAR(255),
    format       VARCHAR(20)  NOT NULL CHECK (format IN ('banner','native','video','push')),
    width        INT,
    height       INT,
    image_url    TEXT,
    title        VARCHAR(255),
    description  TEXT,
    click_url    TEXT,
    adm          TEXT,          -- raw ad markup (for banner/video)
    status       VARCHAR(20)  NOT NULL DEFAULT 'active',
    audit_status VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- RTB — TARGETING RULES
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS targeting_rules (
    id              SERIAL PRIMARY KEY,
    campaign_id     INT          NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    bid_floor       NUMERIC(8,6) DEFAULT 0.01,
    bid_ceiling     NUMERIC(8,6) DEFAULT 5.00,
    geo_countries   TEXT[]       DEFAULT '{}',
    device_types    TEXT[]       DEFAULT '{}',
    os_list         TEXT[]       DEFAULT '{}',
    browser_list    TEXT[]       DEFAULT '{}',
    site_whitelist  TEXT[]       DEFAULT '{}',
    site_blacklist  TEXT[]       DEFAULT '{}',
    hour_schedule   JSONB        DEFAULT '{}',  -- {"0":[0,1,2,...23], "1":[9..18], ...} day->hours
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- RTB — AUCTION LOGS (TimeSeries)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auction_logs (
    id              BIGSERIAL    PRIMARY KEY,
    campaign_id     INT          REFERENCES campaigns(id),
    creative_id     INT          REFERENCES creatives(id),
    bid_request_id  VARCHAR(128),
    imp_id          VARCHAR(128),
    bid_price       NUMERIC(10,6),
    win_price       NUMERIC(10,6),
    clearing_price  NUMERIC(10,6),
    won             BOOLEAN      NOT NULL DEFAULT FALSE,
    ssp_name        VARCHAR(100),
    site_domain     VARCHAR(255),
    app_bundle      VARCHAR(255),
    geo_country     VARCHAR(4),
    device_type     VARCHAR(20),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_auction_logs_created_at   ON auction_logs(created_at DESC);
CREATE INDEX idx_auction_logs_campaign_id  ON auction_logs(campaign_id, created_at DESC);
CREATE INDEX idx_auction_logs_won          ON auction_logs(won, created_at DESC);

-- ─────────────────────────────────────────
-- AUTOPILOT RULES
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rules (
    id           SERIAL PRIMARY KEY,
    campaign_id  INT          NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    level        VARCHAR(20)  NOT NULL DEFAULT 'campaign' CHECK (level IN ('campaign','sub_id','bid')),
    metric       VARCHAR(50)  NOT NULL,
    condition    VARCHAR(10)  NOT NULL CHECK (condition IN ('<','>','<=','>=','==')),
    threshold    NUMERIC(12,6) NOT NULL,
    action       VARCHAR(50)  NOT NULL,
    triggered    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- AUTOPILOT RULE LOGS
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rule_logs (
    id                  BIGSERIAL    PRIMARY KEY,
    rule_id             INT          NOT NULL REFERENCES rules(id),
    campaign_id         INT          NOT NULL REFERENCES campaigns(id),
    triggered_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    metric_value        NUMERIC(12,6),
    action_taken        VARCHAR(100),
    notification_sent   BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_rule_logs_campaign_id ON rule_logs(campaign_id, triggered_at DESC);

-- ─────────────────────────────────────────
-- AD ACCOUNTS (ad networks credentials)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ad_accounts (
    id           SERIAL PRIMARY KEY,
    user_id      INT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform     VARCHAR(50)  NOT NULL,  -- push_house|yandex|kadam|propellerads|google
    name         VARCHAR(255),
    credentials  JSONB        DEFAULT '{}',  -- encrypted in application layer
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    balance      NUMERIC(12,4),
    currency     VARCHAR(8)   DEFAULT 'USD',
    last_sync_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- SETTINGS (key-value per user)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    id       SERIAL PRIMARY KEY,
    user_id  INT          REFERENCES users(id) ON DELETE CASCADE,
    key      VARCHAR(100) NOT NULL,
    value    TEXT,
    UNIQUE(user_id, key)
);

-- ─────────────────────────────────────────
-- LEGACY stats table (backward compat with v2)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stats (
    id           SERIAL PRIMARY KEY,
    link_id      INT          NOT NULL REFERENCES links(id),
    sub_id       VARCHAR(255),
    creative_id  VARCHAR(255),
    clicks       BIGINT       NOT NULL DEFAULT 0,
    conversions  BIGINT       NOT NULL DEFAULT 0,
    bounces      BIGINT       NOT NULL DEFAULT 0,
    total_time   BIGINT       NOT NULL DEFAULT 0,
    cost         NUMERIC(12,6) DEFAULT 0,
    revenue      NUMERIC(12,6) DEFAULT 0,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE(link_id, sub_id, creative_id)
);
