-- Audit F-02 / B14b: наценка на share-ссылках и гостевых доступах — snapshot.
--
-- Модель — товарная цепочка: каждый уровень (superadmin → agency → client)
-- видит net × свою цепочку наценок и, передавая отчёт дальше, может добавить
-- свой процент сверх СВОЕЙ цены. Получатель ссылки/гость видит итог. Чтобы
-- уже выданные отчёты не менялись задним числом при правке users.markup_pct,
-- множитель фиксируется на доступе в момент создания:
--
--   markup_mult_snapshot = chain_mult(создатель) × (1 + markup_extra_pct / 100)
--   public_spend         = net_spend × markup_mult_snapshot
--
-- markup_mode: inherited — создатель ничего не добавил (extra = 0), получатель
-- видит цену создателя; custom_for_share — создатель добавил свой процент.
-- Добавлять (extra > 0) могут только superadmin/admin/agency (rbac.MARKUP_ADD_ROLES).
--
-- Backfill существующих доступов: snapshot = текущая цена владельца (как её
-- считает routers.stats._resolve_scope: цепочка только для agency/client,
-- остальные роли видят net → 1.0). Помечаем такие строки created_by = владелец,
-- поэтому повторный запуск файла их не трогает (идемпотентно).
--
-- revoked (boolean) остаётся для совместимости; источник истины — revoked_at.

-- ---------------------------------------------------------------- share_links
ALTER TABLE share_links
    ADD COLUMN IF NOT EXISTS created_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS markup_extra_pct     NUMERIC(7,3)  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS markup_mult_snapshot NUMERIC(12,6) NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS markup_mode          TEXT          NOT NULL DEFAULT 'inherited',
    ADD COLUMN IF NOT EXISTS revoked_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revoked_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'share_links_markup_mode_chk') THEN
        ALTER TABLE share_links ADD CONSTRAINT share_links_markup_mode_chk
            CHECK (markup_mode IN ('inherited', 'custom_for_share'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'share_links_markup_extra_pct_chk') THEN
        ALTER TABLE share_links ADD CONSTRAINT share_links_markup_extra_pct_chk
            CHECK (markup_extra_pct >= 0 AND markup_extra_pct <= 500);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'share_links_markup_mult_snapshot_chk') THEN
        ALTER TABLE share_links ADD CONSTRAINT share_links_markup_mult_snapshot_chk
            CHECK (markup_mult_snapshot >= 1);
    END IF;
END $$;

-- ---------------------------------------------------------------- guest_access
ALTER TABLE guest_access
    ADD COLUMN IF NOT EXISTS created_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS markup_extra_pct     NUMERIC(7,3)  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS markup_mult_snapshot NUMERIC(12,6) NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS markup_mode          TEXT          NOT NULL DEFAULT 'inherited',
    ADD COLUMN IF NOT EXISTS revoked_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revoked_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'guest_access_markup_mode_chk') THEN
        ALTER TABLE guest_access ADD CONSTRAINT guest_access_markup_mode_chk
            CHECK (markup_mode IN ('inherited', 'custom_for_share'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'guest_access_markup_extra_pct_chk') THEN
        ALTER TABLE guest_access ADD CONSTRAINT guest_access_markup_extra_pct_chk
            CHECK (markup_extra_pct >= 0 AND markup_extra_pct <= 500);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'guest_access_markup_mult_snapshot_chk') THEN
        ALTER TABLE guest_access ADD CONSTRAINT guest_access_markup_mult_snapshot_chk
            CHECK (markup_mult_snapshot >= 1);
    END IF;
END $$;

-- ---------------------------------------------------------------- backfill
-- Цена владельца: произведение (1 + markup_pct/100) по цепочке вверх,
-- только для agency/client — ровно как rbac.spend_multiplier.
WITH RECURSIVE chain AS (
    SELECT id AS root_id, id, parent_user_id, markup_pct FROM users
    UNION ALL
    SELECT c.root_id, u.id, u.parent_user_id, u.markup_pct
    FROM users u JOIN chain c ON u.id = c.parent_user_id
),
owner_price AS (
    SELECT u.id AS owner_id,
           CASE WHEN u.role IN ('agency', 'client')
                THEN exp(sum(ln(1 + COALESCE(c.markup_pct, 0) / 100.0)))
                ELSE 1
           END::numeric(12,6) AS mult
    FROM users u JOIN chain c ON c.root_id = u.id
    GROUP BY u.id, u.role
)
UPDATE share_links sl
   SET created_by_user_id   = sl.user_id,
       markup_mult_snapshot = op.mult,
       markup_extra_pct     = 0,
       markup_mode          = 'inherited',
       revoked_at           = CASE WHEN sl.revoked THEN COALESCE(sl.revoked_at, now()) ELSE sl.revoked_at END
  FROM owner_price op
 WHERE op.owner_id = sl.user_id
   AND sl.created_by_user_id IS NULL;

WITH RECURSIVE chain AS (
    SELECT id AS root_id, id, parent_user_id, markup_pct FROM users
    UNION ALL
    SELECT c.root_id, u.id, u.parent_user_id, u.markup_pct
    FROM users u JOIN chain c ON u.id = c.parent_user_id
),
owner_price AS (
    SELECT u.id AS owner_id,
           CASE WHEN u.role IN ('agency', 'client')
                THEN exp(sum(ln(1 + COALESCE(c.markup_pct, 0) / 100.0)))
                ELSE 1
           END::numeric(12,6) AS mult
    FROM users u JOIN chain c ON c.root_id = u.id
    GROUP BY u.id, u.role
)
UPDATE guest_access ga
   SET created_by_user_id   = ga.owner_id,
       markup_mult_snapshot = op.mult,
       markup_extra_pct     = 0,
       markup_mode          = 'inherited'
  FROM owner_price op
 WHERE op.owner_id = ga.owner_id
   AND ga.created_by_user_id IS NULL;

CREATE INDEX IF NOT EXISTS share_links_owner_active_idx ON share_links (user_id) WHERE revoked_at IS NULL;

COMMENT ON COLUMN share_links.markup_mult_snapshot IS
    'F-02: net × это = цена для получателя. Снято при создании: chain_mult(создатель) × (1 + markup_extra_pct/100). Не пересчитывается.';
COMMENT ON COLUMN share_links.markup_extra_pct IS
    'F-02: процент, добавленный создателем сверх своей цены. > 0 только для superadmin/admin/agency.';
COMMENT ON COLUMN guest_access.markup_mult_snapshot IS
    'F-02: net × это = цена для гостя. Снято при создании: chain_mult(создатель) × (1 + markup_extra_pct/100). Не пересчитывается.';
COMMENT ON COLUMN guest_access.markup_extra_pct IS
    'F-02: процент, добавленный создателем сверх своей цены. > 0 только для superadmin/admin/agency.';
