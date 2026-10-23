-- C30 (docs/ord/DIFF.md): реквизиты иностранных контрагентов для ОРД v6.
-- 059, а не 058: 058_id_sync.sql занят веткой ID sync.
ALTER TABLE counterparties
    ADD COLUMN IF NOT EXISTS region_number       TEXT,  -- код страны по ОКСМ (3 цифры), regionNumber
    ADD COLUMN IF NOT EXISTS tax_id              TEXT,  -- налоговый номер в стране регистрации, taxId
    ADD COLUMN IF NOT EXISTS registration_number TEXT;  -- регистрационный номер, registrationNumber

ALTER TABLE counterparties DROP CONSTRAINT IF EXISTS counterparties_region_number_oksm;
ALTER TABLE counterparties
    ADD CONSTRAINT counterparties_region_number_oksm
    CHECK (region_number IS NULL OR region_number ~ '^[0-9]{3}$');
