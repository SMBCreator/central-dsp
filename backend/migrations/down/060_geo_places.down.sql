-- Откат 060: таблицы GEO v2 аддитивны, на них ссылаются только
-- line_items.targeting.geo_place_ids (JSONB, без FK). Перед откатом
-- выключите GEO_V2_ENABLED: при выключенном флаге биддер не матчит
-- группы с geo_place_ids (fail-closed), удалять ключи из targeting не нужно.
DROP TABLE IF EXISTS geo_ssp_alias;
DROP TABLE IF EXISTS geo_places;
DROP TABLE IF EXISTS geo_dataset_versions;
