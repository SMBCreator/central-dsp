-- 038: single business-day boundary for daily spend (audit M-04).
--
-- `spent_today` used to depend on a cron job at 21:00 UTC to reset it. Now
-- every increment records the business date (Europe/Moscow) it belongs to;
-- readers treat a row whose date is not today as 0 and the cron reset only
-- zeroes rows from previous days, so running it late or twice is harmless.
--
-- Additive and idempotent.

ALTER TABLE campaigns  ADD COLUMN IF NOT EXISTS spent_today_date DATE;
ALTER TABLE line_items ADD COLUMN IF NOT EXISTS spent_today_date DATE;

-- Existing counters belong to the current business day.
UPDATE campaigns  SET spent_today_date = (now() AT TIME ZONE 'Europe/Moscow')::date
    WHERE spent_today_date IS NULL AND spent_today > 0;
UPDATE line_items SET spent_today_date = (now() AT TIME ZONE 'Europe/Moscow')::date
    WHERE spent_today_date IS NULL AND spent_today > 0;
