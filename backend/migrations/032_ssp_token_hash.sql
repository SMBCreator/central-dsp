-- Токены SSP-партнёров (X-CentralDSP-Token) больше не хранятся в открытом виде.
-- auth_token_hash = hex(sha256(token)), auth_token_hint = последние 4 символа
-- (только для отображения в UI). Открытое значение выдаётся один раз из
-- POST /supply-partners/{id}/token и в БД не пишется.
--
-- Порядок выкладки (см. docs/DEPLOYMENT.md, «Ротация схемы токенов SSP»):
--   1) новый bidder-go (понимает auth_token_hash), 2) backend + эта миграция.
-- Старый бинарь Go читал поле auth_token из фида: после миграции оно пустое,
-- поэтому такой бинарь пропустил бы проверку токена — не запускать старый Go
-- поверх новой схемы.

ALTER TABLE supply_partners
    ADD COLUMN IF NOT EXISTS auth_token_hash TEXT,
    ADD COLUMN IF NOT EXISTS auth_token_hint TEXT;

-- Бэкфилл из открытого токена: sha256 — встроенная функция PostgreSQL 11+.
UPDATE supply_partners
   SET auth_token_hash = encode(sha256(convert_to(auth_token, 'UTF8')), 'hex'),
       auth_token_hint = right(auth_token, 4)
 WHERE auth_token IS NOT NULL
   AND auth_token <> ''
   AND auth_token_hash IS NULL;

-- Открытый токен удаляем из схемы целиком, чтобы SELECT * его больше не отдавал.
ALTER TABLE supply_partners DROP COLUMN IF EXISTS auth_token;

-- Поиск партнёра по endpoint_key делается на каждый BidRequest (Python-путь и фид).
CREATE INDEX IF NOT EXISTS supply_partners_endpoint_key_idx
    ON supply_partners (endpoint_key);
