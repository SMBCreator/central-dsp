-- Audit F-02 / B14c: слой атрибуции конверсий.
--
-- conversions — факт (цель × сессия). conversion_attributions — 1:1 к нему:
-- КАК и К ЧЕМУ конверсия привязана. Статусы:
--   attributed     — бид найден, свой, в окне PIXEL_ATTRIBUTION_WINDOW_DAYS;
--                    все измерения бида скопированы сюда (отчёт по любому
--                    измерению без JOIN в партиционированный bid_events);
--   out_of_window  — clid есть, бид найден, но старше окна;
--   unattributed   — clid нет / бид не найден / чужой.
-- Модель — last_click (единственная; поле оставлено под будущие).
-- owner_user_id — владелец пикселя: по нему строятся строки «Без атрибуции» /
-- «Вне окна» на уровне рекламодателя и coverage (у неатрибутированной
-- конверсии нет campaign_id, но есть владелец).
--
-- Backfill: из conversions + последний бид по clid (в пределах 90 дней до
-- конверсии). Идемпотентно: ON CONFLICT (conversion_id) DO NOTHING.

CREATE TABLE IF NOT EXISTS conversion_attributions (
    conversion_id          UUID PRIMARY KEY REFERENCES conversions(id) ON DELETE CASCADE,
    owner_user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
    pixel_id               UUID NOT NULL,
    goal_id                UUID NOT NULL,
    attribution_status     TEXT NOT NULL,
    attribution_model      TEXT NOT NULL DEFAULT 'last_click',
    clid                   TEXT,
    bid_at                 TIMESTAMPTZ,
    converted_at           TIMESTAMPTZ NOT NULL,
    -- измерения бида (NULL для unattributed)
    campaign_id            UUID,
    line_item_id           UUID,
    creative_id            UUID,
    supply_partner_id      UUID,
    domain                 TEXT,
    device_type            TEXT,
    geo_country            TEXT,
    os                     TEXT,
    browser                TEXT,
    ad_type                TEXT,
    tagid                  TEXT,
    publisher_id           TEXT,
    bundle                 TEXT,
    external_conversion_id TEXT,
    raw_payload            JSONB,
    is_test                BOOLEAN NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT conversion_attributions_status_chk
        CHECK (attribution_status IN ('attributed', 'out_of_window', 'unattributed')),
    CONSTRAINT conversion_attributions_model_chk
        CHECK (attribution_model IN ('last_click'))
);

CREATE INDEX IF NOT EXISTS conversion_attributions_owner_time_idx
    ON conversion_attributions (owner_user_id, converted_at);
CREATE INDEX IF NOT EXISTS conversion_attributions_campaign_time_idx
    ON conversion_attributions (campaign_id, converted_at)
    WHERE attribution_status = 'attributed';

COMMENT ON TABLE conversion_attributions IS
    'F-02: как конверсия привязана к биду (last_click). 1:1 к conversions; measurement dims скопированы из бида.';
COMMENT ON COLUMN conversion_attributions.attribution_status IS
    'attributed | out_of_window (бид старше окна) | unattributed (нет clid / бид не найден / чужой)';

-- ---------------------------------------------------------------- backfill
INSERT INTO conversion_attributions (
    conversion_id, owner_user_id, pixel_id, goal_id, attribution_status, attribution_model,
    clid, bid_at, converted_at,
    campaign_id, line_item_id, creative_id, supply_partner_id,
    domain, device_type, geo_country, os, browser, ad_type, tagid, publisher_id, bundle,
    is_test
)
SELECT
    cv.id, p.user_id, cv.pixel_id, cv.goal_id,
    CASE
        WHEN cv.campaign_id IS NOT NULL THEN 'attributed'
        WHEN cv.clid IS NOT NULL AND be.bid_id IS NOT NULL THEN 'out_of_window'
        ELSE 'unattributed'
    END,
    'last_click',
    cv.clid, be.created_at, cv.created_at,
    cv.campaign_id, cv.line_item_id, cv.creative_id, cv.supply_partner_id,
    cv.domain, cv.device_type, cv.geo_country,
    CASE WHEN cv.campaign_id IS NOT NULL THEN be.os END,
    CASE WHEN cv.campaign_id IS NOT NULL THEN be.browser END,
    CASE WHEN cv.campaign_id IS NOT NULL THEN be.ad_type END,
    CASE WHEN cv.campaign_id IS NOT NULL THEN be.tagid END,
    CASE WHEN cv.campaign_id IS NOT NULL THEN be.publisher_id END,
    CASE WHEN cv.campaign_id IS NOT NULL THEN be.bundle END,
    cv.is_test
FROM conversions cv
JOIN pixels p ON p.id = cv.pixel_id
LEFT JOIN LATERAL (
    SELECT b.bid_id, b.created_at, b.os, b.browser, b.ad_type, b.tagid, b.publisher_id, b.bundle
    FROM bid_events b
    WHERE cv.clid IS NOT NULL
      AND b.bid_id = cv.clid AND b.event_type = 'bid'
      AND b.created_at <= cv.created_at
      AND b.created_at >= cv.created_at - interval '90 days'
    ORDER BY b.created_at DESC
    LIMIT 1
) be ON true
ON CONFLICT (conversion_id) DO NOTHING;
