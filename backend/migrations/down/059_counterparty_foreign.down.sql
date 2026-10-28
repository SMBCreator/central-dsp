-- Откат C30 (только вручную, migrate.py down-файлы не читает):
--   psql "$DATABASE_URL" -f backend/migrations/down/059_counterparty_foreign.down.sql
--   затем DELETE FROM schema_migrations WHERE filename = '059_counterparty_foreign.sql';
ALTER TABLE counterparties DROP CONSTRAINT IF EXISTS counterparties_region_number_oksm;
ALTER TABLE counterparties
    DROP COLUMN IF EXISTS registration_number,
    DROP COLUMN IF EXISTS tax_id,
    DROP COLUMN IF EXISTS region_number;
