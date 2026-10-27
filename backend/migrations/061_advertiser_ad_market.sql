-- ТЗ п.5: где планируется реклама рекламодателя — 'ru' (РФ, маркировка 38-ФЗ)
-- или 'foreign' (только за пределами РФ: без ОРД, российские гео недоступны).
-- Аддитивная: существующие рекламодатели получают 'ru', поведение не меняется.
ALTER TABLE advertisers
    ADD COLUMN IF NOT EXISTS ad_market TEXT NOT NULL DEFAULT 'ru';

ALTER TABLE advertisers DROP CONSTRAINT IF EXISTS advertisers_ad_market_check;
ALTER TABLE advertisers
    ADD CONSTRAINT advertisers_ad_market_check CHECK (ad_market IN ('ru', 'foreign'));
