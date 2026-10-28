-- Откат п.5 (только вручную, migrate.py down-файлы не читает):
--   psql "$DATABASE_URL" -f backend/migrations/down/061_advertiser_ad_market.down.sql
--   затем DELETE FROM schema_migrations WHERE filename = '061_advertiser_ad_market.sql';
ALTER TABLE advertisers DROP CONSTRAINT IF EXISTS advertisers_ad_market_check;
ALTER TABLE advertisers DROP COLUMN IF EXISTS ad_market;
