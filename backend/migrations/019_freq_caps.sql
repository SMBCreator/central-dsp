-- 019: multi-window frequency caps (up to 3 windows per campaign)
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS frequency_caps JSONB;
-- Backfill from the legacy single-window pair
UPDATE campaigns
   SET frequency_caps = jsonb_build_array(jsonb_build_object(
         'impressions', frequency_cap_impressions,
         'period_hours', COALESCE(frequency_cap_period_hours, 24)))
 WHERE frequency_caps IS NULL AND frequency_cap_impressions IS NOT NULL;
