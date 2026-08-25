-- 017: Retargeting — audience segments built from Central Pixel events.
-- Membership lives in Postgres (source of truth) + Redis ZSET (hot path).

CREATE TABLE IF NOT EXISTS audience_segments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pixel_id UUID NOT NULL REFERENCES pixels(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    -- rule_type: all_visitors | url_contains | event | goal
    rule_type TEXT NOT NULL DEFAULT 'all_visitors'
        CHECK (rule_type IN ('all_visitors', 'url_contains', 'event', 'goal')),
    rule_value TEXT,                          -- substring / event name / goal id
    ttl_days INT NOT NULL DEFAULT 30 CHECK (ttl_days BETWEEN 1 AND 180),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audience_segments_pixel
    ON audience_segments (pixel_id) WHERE is_active;

CREATE TABLE IF NOT EXISTS audience_members (
    segment_id UUID NOT NULL REFERENCES audience_segments(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (segment_id, device_id)
);

CREATE INDEX IF NOT EXISTS idx_audience_members_expiry
    ON audience_members (expires_at);
