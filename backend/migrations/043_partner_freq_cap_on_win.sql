-- Audit R-06: frequency cap — win is a reservation, imp confirms.
--
-- По OpenRTB win notice (nurl) — «вы победили», а не «показ состоялся»; показ
-- подтверждает наш imp-пиксель внутри adm. Freq cap фиксирует показ на imp,
-- а на win лишь держит бронь с TTL. Некоторые биржи (in-app SDK без
-- клиентского пикселя) не могут дёрнуть imp — для них флаг переключает
-- ОСНОВАНИЕ frequency cap на win. Default false; включать только по
-- документации конкретной SSP.
--
-- Имя флага — про freq cap, не про delivery: wins и impressions в отчётности
-- остаются раздельными колонками, флаг их не смешивает. Ранняя редакция
-- этой миграции называла колонку win_counts_as_imp — переименовываем, если
-- она уже есть (dev-базы), иначе создаём сразу под новым именем.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'supply_partners' AND column_name = 'win_counts_as_imp') THEN
        ALTER TABLE supply_partners RENAME COLUMN win_counts_as_imp TO freq_cap_on_win;
    END IF;
END $$;

ALTER TABLE supply_partners
    ADD COLUMN IF NOT EXISTS freq_cap_on_win BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN supply_partners.freq_cap_on_win IS
    'R-06: основание для frequency cap — win notice вместо /rtb/imp (только SSP без клиентского imp-пикселя). Не влияет на отчётность: impressions считает только пиксель.';
