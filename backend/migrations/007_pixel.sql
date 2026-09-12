-- Central Pixel: umami-class first-party analytics wired into the DSP
CREATE TABLE IF NOT EXISTS pixels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pixel_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pixel_id UUID NOT NULL REFERENCES pixels(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'pageview', -- pageview | event | heartbeat
    event_name TEXT,
    url TEXT,
    referrer TEXT,
    device_type TEXT,
    browser TEXT,
    os TEXT,
    country TEXT,
    duration_sec INT DEFAULT 0,
    clid TEXT,                       -- cdsp_clid: our click/bid id for attribution
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pixel_events_pixel_ts ON pixel_events (pixel_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pixel_events_session ON pixel_events (pixel_id, session_id);
CREATE INDEX IF NOT EXISTS idx_pixel_events_clid ON pixel_events (clid) WHERE clid IS NOT NULL;

CREATE TABLE IF NOT EXISTS goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pixel_id UUID NOT NULL REFERENCES pixels(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    goal_type TEXT NOT NULL DEFAULT 'event',  -- event | url | duration
    match_value TEXT NOT NULL,                -- event name, URL substring, or seconds
    value NUMERIC(12,4) DEFAULT 0,            -- monetary value of a conversion
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    pixel_id UUID NOT NULL REFERENCES pixels(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    clid TEXT,                                -- attributed bid/click id (NULL = organic)
    campaign_id UUID,
    line_item_id UUID,
    creative_id UUID,
    supply_partner_id UUID,
    domain TEXT,                              -- traffic source domain from the bid
    device_type TEXT,
    geo_country TEXT,
    value NUMERIC(12,4) DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (goal_id, session_id)              -- one conversion per goal per session
);
CREATE INDEX IF NOT EXISTS idx_conversions_campaign ON conversions (campaign_id, created_at DESC);

-- Campaign optimization settings
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS pixel_id UUID REFERENCES pixels(id) ON DELETE SET NULL;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS goal_id UUID REFERENCES goals(id) ON DELETE SET NULL;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS objective TEXT NOT NULL DEFAULT 'clicks'; -- clicks | conversions | ctr
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS target_cpa NUMERIC(12,4);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS target_cpc NUMERIC(12,4);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS auto_optimize BOOLEAN NOT NULL DEFAULT FALSE;

-- Optimizer decisions: per-campaign blocked traffic sources
CREATE TABLE IF NOT EXISTS optimizer_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    block_type TEXT NOT NULL,   -- domain | supply_partner | device_type
    block_value TEXT NOT NULL,
    reason TEXT NOT NULL,
    metrics JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, block_type, block_value)
);
CREATE INDEX IF NOT EXISTS idx_optimizer_blocks_campaign ON optimizer_blocks (campaign_id);

-- conversions column in hourly stats for fast CPA math
ALTER TABLE stats_hourly ADD COLUMN IF NOT EXISTS conversions BIGINT NOT NULL DEFAULT 0;
