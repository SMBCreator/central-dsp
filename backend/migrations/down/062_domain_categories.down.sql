-- Откат 062: справочник доменов используется только биддером через
-- GET /rtb-feed/domain-categories; без таблицы фид отдаст пустую карту,
-- биддер продолжит работать по категориям из запроса.
DROP INDEX IF EXISTS domain_categories_source_hits;
DROP TABLE IF EXISTS domain_categories;
