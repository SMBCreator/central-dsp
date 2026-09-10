-- Audit R-06: frequency cap — win is a reservation, imp confirms.
--
-- По OpenRTB win notice (nurl) — «вы победили», а не «показ состоялся»; показ
-- подтверждает наш imp-пиксель внутри adm. Freq cap фиксирует показ на imp,
-- а на win лишь держит бронь с TTL. Некоторые биржи (in-app SDK без
-- клиентского пикселя) не могут дёрнуть imp — для них флаг переключает учёт
-- на win. Default false; включать только по документации конкретной SSP.
ALTER TABLE supply_partners
    ADD COLUMN IF NOT EXISTS win_counts_as_imp BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN supply_partners.win_counts_as_imp IS
    'R-06: freq cap считает показ на win notice (SSP без клиентского imp-пикселя). Default false: показ подтверждает /rtb/imp.';
