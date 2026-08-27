-- Платформенные (глобальные) настройки: ключи интеграций и т.п.
-- Доступ на запись — только super_admin (проверяется на уровне API).
CREATE TABLE IF NOT EXISTS platform_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_by UUID REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
