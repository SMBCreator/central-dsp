-- 028: viewability (MRC: >=50% площади в зоне видимости >=1 сек).
-- Замер вшит в разметку креатива (IntersectionObserver для баннеров,
-- eventtrackers OpenRTB Native 1.2 для натива/push, событие start для видео).
ALTER TABLE stats_hourly ADD COLUMN IF NOT EXISTS viewable_imps BIGINT NOT NULL DEFAULT 0;
ALTER TABLE domain_stats_hourly ADD COLUMN IF NOT EXISTS viewable_imps BIGINT NOT NULL DEFAULT 0;
