-- Audit S-06: жизненный цикл SSP-токена.
--
-- До этой миграции revoke обнулял auth_token_hash, а биддер трактовал пустой
-- хэш как «проверка не требуется» — отзыв токена ОТКРЫВАЛ endpoint. Теперь:
--   * пустой хэш = партнёр не может торговаться (Go/Python отвечают 403,
--     backend не даёт перевести в testing/active без токена);
--   * revoke переводит партнёра в paused и фиксирует token_revoked_at;
--   * ротация атомарна и безразрывна: старый хэш живёт в token_prev_hash до
--     token_prev_valid_until (15 минут), чтобы SSP успел переключить секрет.

ALTER TABLE supply_partners
    ADD COLUMN IF NOT EXISTS token_revoked_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS token_prev_hash        TEXT,
    ADD COLUMN IF NOT EXISTS token_prev_valid_until TIMESTAMPTZ;

COMMENT ON COLUMN supply_partners.token_revoked_at IS
    'Момент отзыва токена (DELETE /supply-partners/{id}/token). Сбрасывается при выпуске нового.';
COMMENT ON COLUMN supply_partners.token_prev_hash IS
    'hex(sha256) предыдущего токена; принимается до token_prev_valid_until (безразрывная ротация).';
COMMENT ON COLUMN supply_partners.token_prev_valid_until IS
    'Срок действия token_prev_hash. NULL или в прошлом = старый токен не принимается.';

-- Партнёры, которые уже торгуются без токена, — угроза: закрываем до выпуска токена.
UPDATE supply_partners
   SET status = 'paused', updated_at = now()
 WHERE status IN ('testing', 'active')
   AND (auth_token_hash IS NULL OR auth_token_hash = '');
