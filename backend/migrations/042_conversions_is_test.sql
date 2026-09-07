-- Audit E-04: сквозной test-флаг до пиксель-конверсий.
--
-- Клик по тестовому биду (OpenRTB test=1) уводит на лендинг с cdsp_clid, и
-- пиксель атрибутирует конверсию кампании как боевую: писалась строка в
-- conversions и stats_hourly.conversions += 1 (CPA-математика, оптимизатор,
-- скоринг доменов). Теперь флаг наследуется от бида: строка в conversions
-- остаётся (журнал/отладка интеграции), но помечена is_test и не попадает ни
-- в hourly, ни в ML/скоринг/оптимизатор.
ALTER TABLE conversions ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;

-- Ретроспективно: конверсии, привязанные к тестовым бидам (034 добавила
-- bid_events.is_test), пометить, чтобы отчёты не считали их.
UPDATE conversions cv
   SET is_test = TRUE
  FROM bid_events be
 WHERE be.bid_id = cv.clid AND be.event_type = 'bid' AND be.is_test
   AND NOT cv.is_test;
