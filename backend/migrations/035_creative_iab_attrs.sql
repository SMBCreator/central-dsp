-- 035: IAB-категории и OpenRTB creative attributes на креативе.
--
-- Зачем: SSP присылает bcat (запрещённые IAB-категории) и imp.*.battr
-- (запрещённые атрибуты креатива: авто-звук, expandable, skippable и т.д.).
-- Без этих полей Go-биддер не может их отфильтровать и отдаёт биды, которые
-- SSP отклонит (или, хуже, покажет и потом заблокирует партнёра).
-- Также bid.cat / bid.attr в ответе — обязательны по многим SSP-контрактам.
--
-- Значения:
--   iab_categories — коды IAB Content Taxonomy 1.0 ("IAB1", "IAB1-1", ...). Пусто = не заявлены.
--   attrs          — OpenRTB 2.5 §5.3 Creative Attributes (1..17). Пусто = стандартный статичный креатив.

ALTER TABLE creatives ADD COLUMN IF NOT EXISTS iab_categories TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE creatives ADD COLUMN IF NOT EXISTS attrs SMALLINT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN creatives.iab_categories IS 'IAB Content Taxonomy 1.0 коды креатива: bid.cat в ответе, фильтр по bcat SSP';
COMMENT ON COLUMN creatives.attrs IS 'OpenRTB Creative Attributes (§5.3): bid.attr в ответе, фильтр по imp.banner/video.battr SSP';
