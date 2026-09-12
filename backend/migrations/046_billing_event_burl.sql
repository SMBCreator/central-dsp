-- Audit R-07: billing by burl — win notice is not the billable event.
--
-- OpenRTB 2.5 §4.3.3: деньги SSP берёт по burl (billing notice), nurl лишь
-- сообщает о победе. До этой миграции DSP списывал расход на nurl; биржа,
-- биллящая по burl, при сверке покажет меньше (win без рендера = мы списали,
-- SSP — нет).
--
-- supply_partners.billing_event — на каком событии возникает подтверждённый
-- расход: 'win' (nurl, поведение до R-07) или 'burl'. Существующие партнёры
-- остаются на 'win' — переключать на burl только по чеклисту онбординга
-- (docs/runbooks/rtb_alerts.md#billing-burl): биржа реально шлёт burl с
-- ${AUCTION_PRICE}, проверены duplicate/late burl и тестовая сверка. Новые
-- партнёры создаются с 'burl' (DEFAULT ниже меняется после backfill).
--
-- stats_hourly.billable_imps — показы, по которым возник расход (burl; в
-- режиме 'win' — сам win). Отдельно от wins (выигрыши) и impressions
-- (доставка по imp-пикселю): billable нельзя подменять ни тем, ни другим.
--
-- bid_events.late — событие пришло после того, как его дедлайн истёк и был
-- зафиксирован (billable после win_expired). Деньги при этом списываются
-- ровно один раз; флаг — для сверки с SSP.
ALTER TABLE supply_partners
    ADD COLUMN IF NOT EXISTS billing_event TEXT NOT NULL DEFAULT 'win';
ALTER TABLE supply_partners ALTER COLUMN billing_event SET DEFAULT 'burl';
ALTER TABLE supply_partners DROP CONSTRAINT IF EXISTS supply_partners_billing_event_check;
ALTER TABLE supply_partners
    ADD CONSTRAINT supply_partners_billing_event_check CHECK (billing_event IN ('win', 'burl'));
COMMENT ON COLUMN supply_partners.billing_event IS
    'R-07: событие подтверждённого расхода: win (nurl, legacy) | burl (OpenRTB billing notice). Существующие партнёры — win; включать burl только после чеклиста онбординга.';

ALTER TABLE stats_hourly
    ADD COLUMN IF NOT EXISTS billable_imps INTEGER NOT NULL DEFAULT 0;
COMMENT ON COLUMN stats_hourly.billable_imps IS
    'R-07: показы с подтверждённым расходом (burl; для billing_event=win — win). Не равно wins и не равно impressions.';

ALTER TABLE bid_events
    ADD COLUMN IF NOT EXISTS late BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN bid_events.late IS
    'R-07: событие после истёкшего дедлайна (billable после win_expired). Деньги списаны один раз; флаг для сверки.';

-- Backfill: до R-07 деньги списывались на win — эти показы и есть billable.
-- Идемпотентно: только строки, где billable_imps ещё не заполнен.
UPDATE stats_hourly SET billable_imps = wins
 WHERE billable_imps = 0 AND wins > 0 AND spend > 0;
