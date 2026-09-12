-- Audit E-02: долговременный dedup для трекинг-событий (impression/click/
-- view/video/test-win).
--
-- Раньше идемпотентность держалась только на Redis SETNX, который ставился
-- ДО записи в БД: временная ошибка после SETNX превращалась в постоянную
-- потерю события до истечения TTL. Теперь заявка на событие — строка в этой
-- таблице в ОДНОЙ транзакции с INSERT bid_events и UPSERT stats_hourly;
-- откат транзакции снимает и заявку, повтор пикселя дописывает всё целиком.
--
-- Отдельная таблица, а не UNIQUE на bid_events: bid_events партиционирована
-- по created_at, и уникальный индекс обязан включать ключ партиции — для
-- дедупа он бесполезен. Строки живут 3 дня (event-токен действует 6 часов),
-- чистка — в /cron/retention.
--
-- win не сюда: его дедуп — transactions(bid_id) WHERE type='spend'
-- (billing_core.bill_win), в той же транзакции, что и событие win.

CREATE TABLE IF NOT EXISTS bid_event_once (
    bid_id     TEXT        NOT NULL,
    kind       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bid_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_bid_event_once_created ON bid_event_once (created_at);
