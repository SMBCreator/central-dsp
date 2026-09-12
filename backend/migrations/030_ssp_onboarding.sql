-- 030: Онбординг SSP — поля, которые SSP присылает в интеграционной анкете.
--
-- Реальный чек-лист подключения SSP <-> DSP:
--   * версия протокола OpenRTB (2.5 / 2.6)
--   * валюта ставок (cur в BidResponse)
--   * tmax — за сколько мс SSP ждёт ответ (обычно 100-300)
--   * IP-адреса серверов SSP (whitelist на нашей стороне)
--   * seat ID, который SSP присвоил нам как байеру
--   * контакт интеграционного менеджера
--   * статус интеграции: draft -> testing -> active (боевой трафик)
--   * результаты последней автопроверки подключения
--
-- Все изменения аддитивные и обратно совместимые.

ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS protocol_version TEXT NOT NULL DEFAULT '2.5';
ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'RUB';
ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS tmax_ms INT NOT NULL DEFAULT 200;
ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS ip_whitelist JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS seat_id TEXT;
ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS contact_email TEXT;
ALTER TABLE supply_partners ADD COLUMN IF NOT EXISTS last_check JSONB;

COMMENT ON COLUMN supply_partners.protocol_version IS 'Версия OpenRTB, заявленная SSP: 2.5 или 2.6';
COMMENT ON COLUMN supply_partners.currency IS 'Валюта ставок (cur в BidResponse): RUB / USD / EUR';
COMMENT ON COLUMN supply_partners.tmax_ms IS 'Таймаут ответа в мс, который требует SSP (справочно; сервер отвечает так быстро, как может)';
COMMENT ON COLUMN supply_partners.ip_whitelist IS 'Массив IP/CIDR серверов SSP. Пустой = принимать с любых IP.';
COMMENT ON COLUMN supply_partners.seat_id IS 'Seat ID, присвоенный нам этим SSP (возвращается в seatbid.seat)';
COMMENT ON COLUMN supply_partners.last_check IS 'Результат последней автопроверки подключения: {ran_at, passed, checks:[{key,label,ok,detail}]}';

-- Статус 'testing': endpoint принимает запросы, но интеграция ещё не боевая.
-- Существующий столбец status TEXT без CHECK — значение просто документируем.
COMMENT ON COLUMN supply_partners.status IS 'draft | testing | active | paused | archived';
