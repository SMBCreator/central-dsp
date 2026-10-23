-- Откат 058_id_sync.sql. migrate.py этот каталог не читает (glob *.sql только
-- в migrations/), выполняется вручную после ID_SYNC_ENABLED=false:
--   psql "$DATABASE_URL" -f backend/migrations/down/058_id_sync.down.sql
--   psql "$DATABASE_URL" -c "DELETE FROM schema_migrations WHERE filename = '058_id_sync.sql'"
-- Redis: redis-cli --scan --pattern 'idsync:*' | xargs -r redis-cli DEL

DROP TABLE IF EXISTS id_sync_map;
ALTER TABLE supply_partners DROP COLUMN IF EXISTS id_sync_salt;
ALTER TABLE supply_partners DROP COLUMN IF EXISTS identity;
