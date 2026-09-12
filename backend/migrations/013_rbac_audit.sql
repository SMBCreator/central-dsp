-- 013: RBAC (6 ролей) + append-only audit_log
-- Роли: superadmin | admin | manager | advertiser | analyst | finance
-- Легаси-роль viewer маппится в analyst.

BEGIN;

UPDATE users SET role = 'analyst' WHERE role = 'viewer';

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('superadmin','admin','manager','advertiser','analyst','finance'));

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID,
    user_email  TEXT,
    user_role   TEXT,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    status_code INT  NOT NULL,
    entity      TEXT,
    entity_id   TEXT,
    payload     JSONB,
    ip          TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_path ON audit_log(path, created_at DESC);

-- append-only: запрещаем UPDATE/DELETE (как у ledger)
CREATE OR REPLACE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_log;
CREATE TRIGGER trg_audit_append_only
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

COMMIT;
