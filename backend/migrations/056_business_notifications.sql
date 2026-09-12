-- Audit C4b / N-03: бизнес-уведомления адресатам сущности.
--
-- notify_emails — адреса для писем о событиях кампании/рекламодателя. Разрешение:
-- campaign.notify_emails → advertiser.notify_emails → email владельца (users);
-- первый непустой уровень; notify_inherit=false на кампании запрещает подъём
-- (пустой список = «писем не слать»).
ALTER TABLE advertisers ADD COLUMN IF NOT EXISTS notify_emails text[] NOT NULL DEFAULT '{}';
ALTER TABLE campaigns   ADD COLUMN IF NOT EXISTS notify_emails text[] NOT NULL DEFAULT '{}';
ALTER TABLE campaigns   ADD COLUMN IF NOT EXISTS notify_inherit boolean NOT NULL DEFAULT true;

-- Почему кампания остановлена системой (balance | total_budget | autorule |
-- moderation | admin). Проставляется тем же UPDATE, что и status='paused', в
-- горячем пути биллинга — письмо отсюда не шлётся, его отправляет
-- cron notify_business_checks по этим полям. Сбрасывается при ручной смене статуса.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS stop_reason text;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS stopped_at timestamptz;

-- Единая точка дедупа события: одна строка = одно событие (kind+entity+окно).
-- Вставка ON CONFLICT DO NOTHING гейтит и in-app, и письмо — они не расходятся.
CREATE TABLE IF NOT EXISTS business_alert_log (
    dedup_key   text PRIMARY KEY,
    kind        text NOT NULL,
    entity_type text NOT NULL,
    entity_id   uuid NOT NULL,
    recipients  text[] NOT NULL DEFAULT '{}',
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_business_alert_log_entity
    ON business_alert_log (entity_type, entity_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_business_alert_log_created
    ON business_alert_log (created_at DESC);
