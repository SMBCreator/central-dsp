-- Профили скоринга качества площадок (пользовательские, поверх платформенного domain_quality).
-- Правила формулируются в терминах наблюдаемых метрик и привязываются к кампании или группе.
-- Приоритет применения в биддере: профиль кампании > профиль группы > без профиля.

CREATE TABLE IF NOT EXISTS scoring_profiles (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT,
    -- Окно исследования в днях: за какой период агрегируется статистика домена.
    window_days  INT  NOT NULL DEFAULT 7 CHECK (window_days BETWEEN 1 AND 90),
    -- Порог активации: домен оценивается только если накопил >= min_impressions показов
    -- и >= min_clicks кликов (иначе данных мало — не блокируем, fail-open).
    min_impressions INT NOT NULL DEFAULT 200 CHECK (min_impressions >= 0),
    min_clicks      INT NOT NULL DEFAULT 0   CHECK (min_clicks >= 0),
    -- Пороги качества. NULL = правило выключено. Домен ниже/хуже порога штрафуется.
    min_ctr          NUMERIC(6,3),   -- %  (ниже — плохо)
    min_viewability  NUMERIC(5,2),   -- %  (ниже — плохо)
    max_bounce_rate  NUMERIC(5,2),   -- %  (выше — плохо), из пикселя
    min_avg_depth    NUMERIC(6,2),   -- стр./сессию (ниже — плохо), из пикселя
    min_time_on_site INT,            -- сек (ниже — плохо), из пикселя
    max_cpa          NUMERIC(14,2),  -- ₽ по цели goal_id (выше — плохо), из пикселя
    goal_id          UUID REFERENCES goals(id) ON DELETE SET NULL,
    pixel_id         UUID REFERENCES pixels(id) ON DELETE SET NULL,
    -- Порог итогового скоринга (0..1). Домены ниже — в блок-лист профиля.
    block_threshold  NUMERIC(4,3) NOT NULL DEFAULT 0.5 CHECK (block_threshold BETWEEN 0 AND 1),
    -- enforce=false: только отчёт (наблюдение), не влияет на закупку.
    enforce      BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scoring_profiles_user ON scoring_profiles(user_id);

-- Привязка профиля к кампании и к группе (одна колонка на каждой сущности).
ALTER TABLE campaigns       ADD COLUMN IF NOT EXISTS scoring_profile_id UUID REFERENCES scoring_profiles(id) ON DELETE SET NULL;
ALTER TABLE campaign_groups ADD COLUMN IF NOT EXISTS scoring_profile_id UUID REFERENCES scoring_profiles(id) ON DELETE SET NULL;

-- Персональный блок-лист доменов по кампании, рассчитанный профилем (enforce=true).
-- Отдельно от optimizer_blocks: тут источник — скоринг, с прозрачной причиной и score.
CREATE TABLE IF NOT EXISTS scoring_blocklist (
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    domain      TEXT NOT NULL,
    score       NUMERIC(4,3) NOT NULL,
    reasons     JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, domain)
);

CREATE INDEX IF NOT EXISTS idx_scoring_blocklist_campaign ON scoring_blocklist(campaign_id);
