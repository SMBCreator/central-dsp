-- B13c (ord_spec.md §7): Central DSP = ОРС (оператор рекламной системы).
-- Ровно одна «наша» организация: в ОРД уходит isOpc=true + rsUrl, на неё
-- регистрируется сводная площадка central-dsp (тип «Сайт», URL рекламной
-- системы — правило РКН для DSP/сетей, по SSP статистика не делится).
-- Статус площадки хранится в platform_settings (key = 'ord_platform').
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS is_self BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE counterparties ADD COLUMN IF NOT EXISTS rs_url  TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_counterparties_self ON counterparties ((1)) WHERE is_self;

-- Бэкфилл: первая заведённая организация с ролью ОРС становится «нашей».
UPDATE counterparties SET is_self = true
 WHERE id = (SELECT id FROM counterparties WHERE role = 'ors' ORDER BY created_at LIMIT 1)
   AND NOT EXISTS (SELECT 1 FROM counterparties WHERE is_self);
