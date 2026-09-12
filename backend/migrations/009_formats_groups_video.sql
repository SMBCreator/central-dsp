-- 009: ad formats (push, subtypes), campaign groups, video metrics, CPM/CPV targets

-- Creative subtype: banner -> static | gif | html5 ; video -> instream | outstream ; push
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS subtype TEXT;
-- Crop rect chosen at upload time: {"x":0,"y":0,"w":1,"h":1} in relative coords
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS crop JSONB;

-- Campaign groups (ad groups): several campaigns share one group
CREATE TABLE IF NOT EXISTS campaign_groups (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS group_id UUID REFERENCES campaign_groups(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_campaigns_group ON campaigns(group_id) WHERE group_id IS NOT NULL;

-- Optimization targets for CPM / CPV
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS target_cpm NUMERIC(12,4);
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS target_cpv NUMERIC(12,4);

-- Video quartile counters in hourly stats
ALTER TABLE stats_hourly ADD COLUMN IF NOT EXISTS video_starts BIGINT NOT NULL DEFAULT 0;
ALTER TABLE stats_hourly ADD COLUMN IF NOT EXISTS video_completes BIGINT NOT NULL DEFAULT 0;
