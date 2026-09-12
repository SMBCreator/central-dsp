-- 033: bid_events -> RANGE-партиционирование по дням + ретенция (GAP-027 / аудит R4).
--
-- Зачем: bid_events — сырой лог RTB (bid/win/impression/click). При 35k QPS
-- даже 1-2% bid-rate даёт сотни млн строк в месяц; DELETE по created_at
-- на такой таблице не масштабируется (bloat, вакуум, блокировки).
-- Партиции по дням: ретенция = DROP PARTITION (мгновенно), отчёты за
-- окно N дней читают только нужные партиции (partition pruning).
--
-- Как: "новая таблица + перенос + атомарный swap". На таблице нет FK и
-- никто не ссылается на bid_events.id, поэтому swap безопасен.
-- Перенос данных — в отдельной транзакции batch'ами? Нет: миграция
-- атомарна (одна транзакция migrate.py). Если таблица уже большая (>50M строк),
-- переносите вручную по инструкции в docs/DEPLOYMENT.md §10 и потом
-- stamp'ните миграцию: python migrate.py --stamp 033_bid_events_partitioned.sql
--
-- Идемпотентность: повторный запуск безопасен (все шаги IF NOT EXISTS /
-- проверка relkind).

-- Функция создания дневных партиций [from_day, to_day]. Идемпотентна.
-- Вызывается из миграции и из /cron/retention (создаёт партиции на неделю вперёд).
CREATE OR REPLACE FUNCTION bid_events_ensure_partitions(from_day DATE, to_day DATE)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    d        DATE := from_day;
    created  INTEGER := 0;
    pname    TEXT;
    parent   TEXT;
BEGIN
    -- Родитель — либо bid_events (после swap), либо bid_events_p (во время миграции).
    SELECT c.relname INTO parent
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'p'
      AND c.relname IN ('bid_events', 'bid_events_p')
    ORDER BY c.relname = 'bid_events' DESC LIMIT 1;
    IF parent IS NULL THEN
        RAISE EXCEPTION 'partitioned bid_events not found';
    END IF;

    WHILE d <= to_day LOOP
        pname := 'bid_events_' || to_char(d, 'YYYYMMDD');
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = pname) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                pname, parent, d::timestamptz, (d + 1)::timestamptz
            );
            created := created + 1;
        END IF;
        d := d + 1;
    END LOOP;
    RETURN created;
END $$;

-- Функция ретенции: DROP партиций старше keep_days. Возвращает список удалённых.
-- DEFAULT-партицию не трогаем; legacy-таблицу не трогаем.
CREATE OR REPLACE FUNCTION bid_events_drop_old_partitions(keep_days INTEGER)
RETURNS TEXT[] LANGUAGE plpgsql AS $$
DECLARE
    cutoff   DATE := current_date - keep_days;
    r        RECORD;
    dropped  TEXT[] := '{}';
    part_day DATE;
BEGIN
    FOR r IN
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = 'bid_events' AND c.relname ~ '^bid_events_[0-9]{8}$'
    LOOP
        part_day := to_date(substr(r.relname, 12, 8), 'YYYYMMDD');
        IF part_day < cutoff THEN
            EXECUTE format('DROP TABLE %I', r.relname);
            dropped := array_append(dropped, r.relname);
        END IF;
    END LOOP;
    RETURN dropped;
END $$;

-- 0. Уже партиционирована? Ничего не делаем.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'bid_events' AND c.relkind = 'p'
    ) THEN
        RAISE NOTICE 'bid_events already partitioned, skipping';
        RETURN;
    END IF;

    -- 1. Новая партиционированная таблица — те же колонки, что и текущая.
    --    PK обязан включать ключ партиционирования: (id, created_at).
    CREATE TABLE bid_events_p (
        id                BIGINT GENERATED ALWAYS AS IDENTITY,
        event_type        TEXT NOT NULL,
        bid_id            TEXT NOT NULL,
        imp_id            TEXT,
        campaign_id       UUID,
        line_item_id      UUID,
        creative_id       UUID,
        supply_partner_id UUID,
        price             NUMERIC(12,4),
        clear_price       NUMERIC(12,4),
        device_type       TEXT,
        geo_country       TEXT,
        ad_type           TEXT,
        domain            TEXT,
        os                TEXT,
        browser           TEXT,
        pctr              DOUBLE PRECISION,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (id, created_at)
    ) PARTITION BY RANGE (created_at);

    -- 2. Индексы объявляются на родителе — наследуются каждой партицией.
    CREATE INDEX idx_bid_events_p_bid_id ON bid_events_p (bid_id);
    CREATE INDEX idx_bid_events_p_created ON bid_events_p (created_at DESC);
    CREATE INDEX idx_bid_events_p_campaign_created ON bid_events_p (campaign_id, created_at DESC);
    CREATE INDEX idx_bid_events_p_campaign_created_domain
        ON bid_events_p (campaign_id, created_at DESC, domain)
        WHERE domain IS NOT NULL;

    -- 3. Партиции: покрываем диапазон существующих данных + 7 дней вперёд.
    --    DEFAULT-партиция ловит всё, что не попало в явные (страховка от потери
    --    событий, если cron создания партиций отстал).
    CREATE TABLE bid_events_p_default PARTITION OF bid_events_p DEFAULT;
    PERFORM bid_events_ensure_partitions(
        COALESCE((SELECT date_trunc('day', min(created_at))::date FROM bid_events), current_date),
        current_date + 7
    );

    -- 4. Перенос данных (одна транзакция; для больших таблиц — см. шапку файла).
    INSERT INTO bid_events_p (event_type, bid_id, imp_id, campaign_id, line_item_id, creative_id,
                              supply_partner_id, price, clear_price, device_type, geo_country,
                              ad_type, domain, os, browser, pctr, created_at)
    OVERRIDING SYSTEM VALUE
    SELECT event_type, bid_id, imp_id, campaign_id, line_item_id, creative_id,
           supply_partner_id, price, clear_price, device_type, geo_country,
           ad_type, domain, os, browser, pctr, created_at
    FROM bid_events;

    -- 5. Атомарный swap. Старую таблицу оставляем как bid_events_legacy —
    --    удалить вручную после проверки (DROP TABLE bid_events_legacy).
    ALTER TABLE bid_events RENAME TO bid_events_legacy;
    ALTER TABLE bid_events_p RENAME TO bid_events;
    ALTER INDEX idx_bid_events_p_bid_id RENAME TO idx_bid_events_bid_id_p;
    ALTER INDEX idx_bid_events_p_created RENAME TO idx_bid_events_created_p;
    ALTER INDEX idx_bid_events_p_campaign_created RENAME TO idx_bid_events_campaign_created_p;
    ALTER INDEX idx_bid_events_p_campaign_created_domain RENAME TO idx_bid_events_campaign_created_domain_p;
    ALTER TABLE bid_events_p_default RENAME TO bid_events_default;
END $$;
