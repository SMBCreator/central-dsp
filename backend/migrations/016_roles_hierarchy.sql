-- 016: иерархия ролей (superadmin -> manager/agency -> client), наценки, ограничения редактирования
-- Роли расширяются: + agency (агентство), + client (клиент со статистикой/self-serve по флагу)

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS markup_pct NUMERIC(6,2) NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_users_parent ON users(parent_user_id);

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('superadmin','admin','manager','advertiser','analyst','finance','agency','client'));

-- защита от отрицательных/абсурдных наценок
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_markup_check;
ALTER TABLE users ADD CONSTRAINT users_markup_check CHECK (markup_pct >= 0 AND markup_pct <= 500);

COMMIT;
