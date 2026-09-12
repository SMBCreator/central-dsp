-- 026: ML-слой (Часть B) — pCTR-модель, качество доменов, clean-traffic режим.
--
-- Принципы (skill: Optimization and ML):
--  * модель версионируется в ml_models: датасет-окно, метрики, статус;
--  * domain_quality — производный скоринг, пересчитывается кроном, история не переписывается;
--  * clean_traffic_only — тумблер кампании: биддить только на домены с score >= порога;
--  * bid_events.pctr — decision log: какой pCTR был на момент бида (для offline-оценки).

-- Версионированные модели/агрегаты pCTR
CREATE TABLE IF NOT EXISTS ml_models (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_type    TEXT NOT NULL,                       -- pctr | domain_quality
    version       INT  NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',      -- active | retired
    trained_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_from   TIMESTAMPTZ,                         -- окно обучающих данных
    window_to     TIMESTAMPTZ,
    sample_events BIGINT,                              -- сколько событий в выборке
    metrics       JSONB NOT NULL DEFAULT '{}',         -- {global_ctr, segments, coverage,...}
    params        JSONB NOT NULL DEFAULT '{}',         -- смузинг-приоры и версия формулы
    UNIQUE (model_type, version)
);
CREATE INDEX IF NOT EXISTS idx_ml_models_type_status ON ml_models (model_type, status);

-- pCTR-агрегаты по сегментам (модельные веса; сегмент = ключ фичи)
CREATE TABLE IF NOT EXISTS ml_pctr_segments (
    model_version INT  NOT NULL,
    segment_type  TEXT NOT NULL,       -- domain | creative | device_type | hour
    segment_key   TEXT NOT NULL,       -- значение сегмента
    imps          BIGINT NOT NULL DEFAULT 0,
    clicks        BIGINT NOT NULL DEFAULT 0,
    ctr_smoothed  DOUBLE PRECISION NOT NULL,  -- Beta-сглаженный CTR сегмента
    PRIMARY KEY (model_version, segment_type, segment_key)
);

-- Скоринг качества доменов (пересчитывается кроном; snapshot-семантика)
CREATE TABLE IF NOT EXISTS domain_quality (
    domain        TEXT PRIMARY KEY,
    score         DOUBLE PRECISION NOT NULL,           -- 0..1 (1 = чистый)
    imps          BIGINT NOT NULL DEFAULT 0,
    clicks        BIGINT NOT NULL DEFAULT 0,
    wins          BIGINT NOT NULL DEFAULT 0,
    reasons       JSONB NOT NULL DEFAULT '[]',         -- ["ctr_anomaly","click_burst",...]
    model_version INT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_domain_quality_score ON domain_quality (score);

-- Тумблер кампании: закупать только «чистый» трафик
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS clean_traffic_only BOOLEAN NOT NULL DEFAULT FALSE;

-- Decision log: pCTR на момент бида (NULL = модель не применялась)
ALTER TABLE bid_events ADD COLUMN IF NOT EXISTS pctr DOUBLE PRECISION;
