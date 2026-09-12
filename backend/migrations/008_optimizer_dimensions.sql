-- Optimizer over any dimension: log OS and browser on the bidder side too
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS os TEXT;
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS browser TEXT;

CREATE INDEX IF NOT EXISTS idx_bid_events_campaign_created
  ON bid_events (campaign_id, created_at DESC);
