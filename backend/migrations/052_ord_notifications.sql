-- B13g (ord_spec §7): ошибки ЕРИР, которые ОРД сообщает ПОСЛЕ успешной регистрации
-- объекта (POST /api/external/notification/error_list у Ozon). Объект остаётся
-- принятым ОРД (ord_external_id сохраняется), но получает статус erir_error и
-- текст ошибки; успешная повторная регистрация возвращает registered и
-- закрывает уведомления объекта (resolved_at).
CREATE TABLE IF NOT EXISTS ord_notifications (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider           text NOT NULL,
    notification_id    bigint NOT NULL,
    object_type        text NOT NULL,
    object_external_id text,
    external_id        text,
    owner_id           uuid REFERENCES users(id) ON DELETE SET NULL,
    text               text NOT NULL DEFAULT '',
    url                text,
    ord_date           timestamptz,
    ord_updated_at     timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    resolved_at        timestamptz,
    resolved_reason    text,
    CONSTRAINT ord_notifications_provider_id_uq UNIQUE (provider, notification_id),
    CONSTRAINT ord_notifications_object_type_chk CHECK (
        object_type IN ('organisation', 'contract', 'creative', 'invoice', 'platform', 'unknown')
    )
);
CREATE INDEX IF NOT EXISTS idx_ord_notifications_open
    ON ord_notifications (created_at) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ord_notifications_object
    ON ord_notifications (object_type, external_id);

-- Акты: ЕРИР может отклонить акт после того, как ОРД его принял.
ALTER TABLE ord_invoices DROP CONSTRAINT IF EXISTS ord_invoices_status_chk;
ALTER TABLE ord_invoices ADD CONSTRAINT ord_invoices_status_chk
    CHECK (status IN ('draft', 'submitting', 'ok', 'partial', 'failed', 'erir_error'));
