-- 057: bid_events — создание партиций, устойчивое к строкам в DEFAULT (аудит O-06 / C5).
--
-- Проблема (воспроизведена на PG 15): если в bid_events_default уже лежат строки
-- за день D, то `CREATE TABLE ... PARTITION OF bid_events FOR VALUES FROM (D) TO (D+1)`
-- падает с "updated partition constraint for default partition would be violated
-- by some row". Так падал /cron/retention целиком: партиции вперёд не создавались,
-- gauge bid_events_default_partition_rows не обновлялся, а следующие дни тоже
-- уходили в DEFAULT — проблема сама себя усиливала.
--
-- Решение: партиция создаётся как отдельная таблица (LIKE родителя), строки за её
-- диапазон переносятся из DEFAULT в неё, затем ATTACH PARTITION. Всё — внутри одной
-- транзакции вызывающего, поэтому строки либо переехали вместе с появлением
-- партиции, либо ничего не изменилось. Перенос идёт по явному списку колонок
-- родителя (после 033 к таблице добавлялись колонки — 034, 046), а не через
-- SELECT *, чтобы не зависеть от порядка атрибутов.
--
-- Гонка: между DELETE из DEFAULT и ATTACH конкурентная вставка «опоздавшей» строки
-- за тот же день снова попала бы в DEFAULT, и ATTACH упал бы при проверке. Поэтому
-- перед переносом DEFAULT явно блокируется в ACCESS EXCLUSIVE: ту же блокировку
-- ATTACH PARTITION берёт на DEFAULT-партицию в любом случае, так что набор
-- блокировок не расширяется — меняется только момент захвата. Обычные вставки
-- (в существующие дневные партиции) DEFAULT не трогают и не ждут.
--
-- PG 17+: таблица с собственной identity-колонкой не может быть присоединена как
-- партиция, поэтому LIKE без INCLUDING IDENTITY — единственный переносимый вариант.
--
-- Сигнатура прежняя — вызовы из /cron/retention и runbook не меняются.
-- Идемпотентно: CREATE OR REPLACE.

CREATE OR REPLACE FUNCTION bid_events_ensure_partitions(from_day DATE, to_day DATE)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    d         DATE := from_day;
    created   INTEGER := 0;
    pname     TEXT;
    parent    TEXT;
    def_name  TEXT;
    cols      TEXT;
    lo        TIMESTAMPTZ;
    hi        TIMESTAMPTZ;
BEGIN
    -- Родитель — либо bid_events (после swap), либо bid_events_p (во время миграции 033).
    SELECT c.relname INTO parent
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'p'
      AND c.relname IN ('bid_events', 'bid_events_p')
    ORDER BY c.relname = 'bid_events' DESC LIMIT 1;
    IF parent IS NULL THEN
        RAISE EXCEPTION 'partitioned bid_events not found';
    END IF;

    -- DEFAULT-партиция этого родителя (bid_events_default / bid_events_p_default).
    SELECT c.relname INTO def_name
    FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_class p ON p.oid = i.inhparent
    WHERE p.relname = parent AND c.relpartbound IS NOT NULL
      AND pg_get_expr(c.relpartbound, c.oid) = 'DEFAULT';

    -- Явный список колонок родителя в порядке attnum.
    SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) INTO cols
    FROM pg_attribute a
    WHERE a.attrelid = ('public.' || quote_ident(parent))::regclass
      AND a.attnum > 0 AND NOT a.attisdropped;

    WHILE d <= to_day LOOP
        pname := 'bid_events_' || to_char(d, 'YYYYMMDD');
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = pname) THEN
            lo := d::timestamptz;
            hi := (d + 1)::timestamptz;
            -- 1. Отдельная таблица той же формы. Без INCLUDING IDENTITY: id после
            --    ATTACH генерирует identity родителя при маршрутизации вставки.
            EXECUTE format(
                'CREATE TABLE %I (LIKE %I INCLUDING DEFAULTS INCLUDING CONSTRAINTS)',
                pname, parent
            );
            -- 2. Строки за этот день из DEFAULT — в новую таблицу (если DEFAULT есть).
            --    Блокировка до DELETE: см. шапку файла (гонка с опоздавшей вставкой).
            IF def_name IS NOT NULL THEN
                EXECUTE format('LOCK TABLE %I IN ACCESS EXCLUSIVE MODE', def_name);
                EXECUTE format(
                    'WITH moved AS (DELETE FROM %I WHERE created_at >= %L AND created_at < %L RETURNING %s) '
                    'INSERT INTO %I (%s) SELECT %s FROM moved',
                    def_name, lo, hi, cols, pname, cols, cols
                );
            END IF;
            -- 3. Подключить как партицию. Индексы родителя (в т.ч. PK) создаются на ней
            --    автоматически при ATTACH.
            EXECUTE format(
                'ALTER TABLE %I ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)',
                parent, pname, lo, hi
            );
            created := created + 1;
        END IF;
        d := d + 1;
    END LOOP;
    RETURN created;
END $$;

-- Горизонт партиций: на сколько дней вперёд от сегодня существует последняя
-- дневная партиция. NULL — партиций нет. Используется /cron/retention для gauge
-- bid_events_partition_horizon_days и алерта BidEventsPartitionHorizonShort:
-- измеряем состояние таблицы, а не факт запуска cron.
CREATE OR REPLACE FUNCTION bid_events_partition_horizon_days()
RETURNS INTEGER LANGUAGE sql STABLE AS $$
    SELECT max(to_date(substr(c.relname, 12, 8), 'YYYYMMDD')) - current_date
    FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_class p ON p.oid = i.inhparent
    WHERE p.relname = 'bid_events' AND c.relname ~ '^bid_events_[0-9]{8}$'
$$;
