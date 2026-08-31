-- 025: роль viewer (гостевой отчётный доступ, /report).
-- Багфикс: share.py создаёт гостей с role='viewer', но constraint из 016 её не допускал —
-- INSERT падал бы на живой БД. Расширяем список ролей.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('superadmin','admin','manager','advertiser','analyst','finance','agency','client','viewer'));
