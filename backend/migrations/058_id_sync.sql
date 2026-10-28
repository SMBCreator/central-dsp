-- 058: ID sync / cookie matching (план predeploy, раздел A.4–A.5).
--
-- Аддитивно: две nullable-колонки в supply_partners и новая таблица. Флаг
-- ID_SYNC_ENABLED (по умолчанию off) — пока он выключен, ни одна строка кода
-- в эти объекты не пишет и биддер их не читает.
--
-- supply_partners.identity — JSON-конфиг партнёра (A.5): sync_enabled,
--   sync_in_param, sync_callback_url_template, sync_redirect_hosts[],
--   id_source, ttl_days, requires_consent. NULL = sync не настроен.
-- supply_partners.id_sync_salt — соль хэша чужого ID (sha256(salt || ssp_uid)).
--   Генерируется при первом сохранении identity. Отдельная колонка, чтобы
--   PATCH identity её не перетирал: смена соли обнулила бы все соответствия.
--
-- id_sync_map — durable-хранилище соответствий; горячая копия в Redis
--   idsync:{partner_id}:{hex(ssp_uid_hash)} -> dsp_uid (TTL = expires_at).
--   Чужой ID хранится только хэшем. Откат: docs/ID_SYNC.md (флаг off + down-скрипт).

ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS identity JSONB;
ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS id_sync_salt TEXT;

CREATE TABLE IF NOT EXISTS id_sync_map (
    partner_id    UUID NOT NULL REFERENCES supply_partners(id) ON DELETE CASCADE,
    ssp_uid_hash  BYTEA NOT NULL,
    dsp_uid       TEXT NOT NULL,
    consent_scope TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (partner_id, ssp_uid_hash)
);

CREATE INDEX IF NOT EXISTS id_sync_map_dsp_uid_idx ON id_sync_map (dsp_uid);
CREATE INDEX IF NOT EXISTS id_sync_map_expires_at_idx ON id_sync_map (expires_at);
