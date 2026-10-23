-- GEO v2 (docs/GEO.md, план B.3): справочник мест, версии импорта и маппинг
-- значений конкретного SSP. Аддитивно: существующие таблицы и ключи
-- targeting (geo_countries/geo_regions/geo_cities) не меняются.
-- 060: 058 занят ID sync, 059 — C30 (реквизиты иностранных контрагентов).

CREATE TABLE IF NOT EXISTS geo_dataset_versions (
    version      TEXT PRIMARY KEY,           -- geonames-YYYYMMDD-<sha8 входных файлов>
    source       TEXT NOT NULL,
    license      TEXT NOT NULL,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    places_count INT NOT NULL DEFAULT 0,
    is_active    BOOLEAN NOT NULL DEFAULT false,
    meta         JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS geo_dataset_versions_one_active
    ON geo_dataset_versions (is_active) WHERE is_active;

-- id = geonameid: стабилен между импортами, поэтому geo_place_ids в
-- line_items.targeting переживают обновление справочника.
CREATE TABLE IF NOT EXISTS geo_places (
    id              BIGINT PRIMARY KEY,
    country_iso     CHAR(2) NOT NULL,
    region_code     TEXT,                    -- ISO 3166-2 (только type=region)
    name_ru         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    name_ru_norm    TEXT NOT NULL,           -- geo_norm.geo_normalize(name_ru)
    name_en_norm    TEXT NOT NULL,
    aliases         TEXT[] NOT NULL DEFAULT '{}',  -- нормализованные синонимы
    type            TEXT NOT NULL CHECK (type IN ('country', 'region', 'city', 'town')),
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    population      BIGINT NOT NULL DEFAULT 0,
    parent_id       BIGINT REFERENCES geo_places(id) DEFERRABLE INITIALLY DEFERRED,
    source          TEXT NOT NULL DEFAULT 'geonames',
    dataset_version TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS geo_places_country_type ON geo_places (country_iso, type);
CREATE INDEX IF NOT EXISTS geo_places_parent ON geo_places (parent_id);
CREATE UNIQUE INDEX IF NOT EXISTS geo_places_region_code ON geo_places (region_code) WHERE region_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS geo_places_name_ru_prefix ON geo_places (name_ru_norm text_pattern_ops);
CREATE INDEX IF NOT EXISTS geo_places_name_en_prefix ON geo_places (name_en_norm text_pattern_ops);
CREATE INDEX IF NOT EXISTS geo_places_aliases ON geo_places USING GIN (aliases);

-- Значение конкретного SSP -> наш place_id. Заполняется по образцам запросов
-- при подключении партнёра (и по метрике geo_unmatched_values_total).
CREATE TABLE IF NOT EXISTS geo_ssp_alias (
    id          BIGSERIAL PRIMARY KEY,
    partner_id  UUID NOT NULL REFERENCES supply_partners(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('region', 'city')),
    ssp_value   TEXT NOT NULL,               -- как пришло в device.geo.region/city
    value_norm  TEXT NOT NULL,               -- geo_normalize(ssp_value)
    place_id    BIGINT NOT NULL REFERENCES geo_places(id) ON DELETE CASCADE,
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (partner_id, kind, value_norm)
);
CREATE INDEX IF NOT EXISTS geo_ssp_alias_place ON geo_ssp_alias (place_id);
