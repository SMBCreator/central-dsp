-- 022: creative moderation, notifications, advertiser info (OGRN), creative->counterparty link

-- Moderation fields on creatives
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS moderation_comment TEXT;
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS moderated_by UUID REFERENCES users(id);
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS moderated_at TIMESTAMPTZ;
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS counterparty_id UUID REFERENCES counterparties(id);

-- OGRN on counterparties
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS ogrn TEXT;

-- Notifications
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    link TEXT,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON notifications (user_id, created_at DESC) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_creatives_status_pending ON creatives (status) WHERE status = 'pending';
