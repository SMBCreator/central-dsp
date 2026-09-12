# Партиции `bid_events`: горизонт, DEFAULT-партиция, апгрейд большой таблицы

Сырой RTB-лог `bid_events` партиционирован по дням (миграция 033); строки за день без
партиции падают в `bid_events_default`. Миграция 057 делает создание партиций
устойчивым к этому (`bid_events_ensure_partitions` сама переносит строки за
создаваемый день из DEFAULT атомарно), добавляет функцию
`bid_events_partition_horizon_days()` и метрику `bid_events_partition_horizon_days`.
Все ручные операции — через `backend/scripts/partition_repair.py` (запускать внутри
контейнера backend: `docker compose exec backend python scripts/partition_repair.py <cmd>`).

Коды выхода скрипта: `0` ок · `1` ошибка (в т.ч. границы партиций не в UTC, см. [tz](#tz)) ·
`2` есть что чинить / проверка не сошлась · `3` уже идёт другой запуск ·
`4` таблица не партиционирована (033 не накатана).

Первая команда всегда `status` — она ничего не меняет:

```
partitions: 98  first=2026-06-14  last=2026-11-11  horizon_days=60
DEFAULT partition: empty
```

## horizon — BidEventsPartitionHorizonShort

**Что значит:** до последней существующей дневной партиции меньше 7 дней (`-1` —
партиций нет вообще). Норма — `BID_EVENTS_PARTITIONS_AHEAD_DAYS` (60). Метрика
измеряет состояние таблицы при каждом запуске `/cron/retention`, так что алерт
горит и тогда, когда cron формально «успешен», но партиции не создались.

**Действия:**
1. `GET /scheduler/jobs` → задача `retention`: `last_status`, `last_error`
   (см. [rtb_alerts.md#cron](rtb_alerts.md#cron)). Типичные причины: cron не
   запускался; упал на предыдущем шаге (purge inbox/outbox); staged-таблица с
   именем партиции мешает создать день (см. ниже).
2. Создать партиции вперёд руками — безопасно в любое время:
   `partition_repair.py ensure` (по умолчанию на `BID_EVENTS_PARTITIONS_AHEAD_DAYS`
   вперёд, `--ahead N` переопределяет). Либо запустить сам cron:
   `curl -H "Authorization: Bearer $CRON_SECRET" https://<host>/cron/retention` —
   в ответе `partition_horizon_days` и `partitions_created`.
3. `status` → `horizon_days` ≥ 7. Алерт снимется после следующего запуска cron
   (gauge обновляется только им).

## default — BidEventsDefaultPartitionNonEmpty

**Что значит:** в `bid_events_default` лежат строки — на момент вставки не было
партиции за их день. Данные не потеряны, но запросы по ним медленнее, и ретенция
их не удалит. С 057 строки за **будущие/сегодняшний** дни cron переносит сам при
создании партиции; строки за **прошлые** дни cron намеренно не трогает — это
ручное действие, чтобы объём переноса и момент блокировки выбирал оператор.

**Действия:**
1. `partition_repair.py status` — сколько строк, за какие дни, сколько из них
   старше retention (`expired`), есть ли незавершённые staged-таблицы.
2. `partition_repair.py repair-default --dry-run` — план без изменений.
3. `partition_repair.py repair-default` (батч по умолчанию 20 000 строк,
   `--batch N`). Для каждого дня:
   - день ≥ сегодня — атомарно через `bid_events_ensure_partitions` (одна
     транзакция, DEFAULT под ACCESS EXCLUSIVE на время переноса; поэтому не
     запускать в пик, если в DEFAULT много строк за сегодня);
   - день < сегодня — staged: отдельная таблица `bid_events_YYYYMMDD` без
     подключения к родителю, перенос батчами в коротких транзакциях, затем
     `ATTACH PARTITION`. Прерванный запуск возобновляется той же командой
     (`status` покажет staged-таблицу).
   Строки старше `BID_EVENTS_RETENTION_DAYS` остаются в DEFAULT (код выхода 2) —
   удалить явно: `repair-default --purge-expired` (ретенция и так бы их снесла).
4. `status` → `DEFAULT partition: empty`; следующий запуск cron обнулит gauge.

Репетиция (PG 16, синтетика): 103 000 строк за 41 день, батч 20 000 → 1,5 с,
`left_in_default=0`, общее число строк до/после совпало.

## big-table — апгрейд на 033 при большой таблице

Миграция 033 на свежей/маленькой базе копирует данные внутри одной транзакции;
на проде с историей это часы блокировки записи. **Не правьте файл 033**: `migrate.py`
хранит sha256 каждого применённого файла, и изменённый файл заблокирует все
следующие деплои (`was modified after being applied`). Вместо этого подменяем
таблицу пустой: 033 отрабатывает мгновенно, история доливается потом, при живом
backend, по дню и с возобновлением.

Проверить заранее: `psql -c "\d+ bid_events"` — на таблицу не должны ссылаться
view/FK/trigger (в репозитории таких нет; проверено на репетиции).

```bash
# 0. Окно низкого трафика. Backend остановлен: иначе события, записанные между
#    шагами 1 и 2, окажутся в пустой bid_events_legacy и потеряются на шаге 3.
docker compose stop backend

# 1. Подменить таблицу пустой копией (мгновенно). Старая остаётся как bid_events_old.
psql "$DATABASE_URL" <<'SQL'
BEGIN;
ALTER TABLE bid_events RENAME TO bid_events_old;
CREATE TABLE bid_events (LIKE bid_events_old INCLUDING ALL);
COMMIT;
SQL

# 2. Обычный прогон миграций: 033 видит пустую bid_events (копирует 0 строк, swap),
#    затем 034..057 применяются к партиционированной таблице.
docker compose run --rm backend python migrate.py

# 3. bid_events_legacy сейчас — пустая копия из шага 1. Заменить её настоящей историей.
psql "$DATABASE_URL" -c "SELECT count(*) FROM bid_events_legacy"     # должно быть 0
psql "$DATABASE_URL" <<'SQL'
BEGIN;
DROP TABLE bid_events_legacy;
ALTER TABLE bid_events_old RENAME TO bid_events_legacy;
COMMIT;
SQL

# 4. Запустить backend — новые события идут в партиции. Сразу расширить горизонт
#    (033 создаёт партиции лишь на 7 дней вперёд, cron доведёт до 60 только ночью).
docker compose start backend
docker compose exec backend python scripts/partition_repair.py ensure

# 5. Долить историю в окне retention (по дню, каждая транзакция — один день,
#    повтор безопасен: копируются только отсутствующие строки).
docker compose exec backend python scripts/partition_repair.py backfill-legacy --dry-run
docker compose exec backend python scripts/partition_repair.py backfill-legacy    # rc 0 = verify прошёл

# 6. Контроль и удаление истории.
docker compose exec backend python scripts/partition_repair.py verify-legacy      # missing=0
docker compose exec backend python scripts/partition_repair.py status
psql "$DATABASE_URL" -c "DROP TABLE bid_events_legacy"
```

Что важно знать:
- `id` строк при доливке генерируется заново (как и в самой 033); в коде `bid_events.id`
  никем не используется как курсор — проверено (`grep` по backend/bidder-go).
- Колонки, добавленные после 033 (034: `tagid`, `publisher_id`, `bidfloor`, `loss_reason`…),
  в старой таблице отсутствуют — скрипт копирует пересечение колонок, остальные `NULL`.
- Строки старше `BID_EVENTS_RETENTION_DAYS` не доливаются (ретенция удалила бы их
  сразу). Если нужны для ML/аудита — выгрузить из `bid_events_legacy` до шага 6.
- Между шагами 1 и 2 запись в `bid_events` даёт ошибку только если backend не был
  остановлен — на шаге 0 это исключено.

Репетиция (PG 16, синтетика 300 000 строк / 120 дней, 72 500 из них старше retention):
шаг 1 — 10 мс; шаг 2 — 25 миграций, секунды; backfill 227 500 строк / 91 день — 16 с;
md5 содержимого окна в legacy и в партициях совпал; строки, записанные «живым»
backend между шагами 4 и 5, не задеты; `verify-legacy missing=0`.

## legacy — bid_events_legacy после 033

`status` сообщает, что таблица ещё существует. `verify-legacy` (rc 2 = есть
недоставленные строки в окне retention) → `backfill-legacy` → `verify-legacy` (rc 0)
→ `DROP TABLE bid_events_legacy`. Пока таблица есть, планировщик её не видит и на
отчёты она не влияет — это просто занятое место.

## tz — «MISALIGNED partition bounds»

Границы партиций backend создаёт как `день::timestamptz` в **своей** session
TimeZone (наследуется от сервера PG). Скрипт работает в UTC и перед переносом
проверяет, что нижние границы существующих партиций — полночь UTC; иначе
`repair-default` останавливается (rc 1), а `status` печатает список. Это означает,
что у сервера PG задан не-UTC `timezone` — привести его к UTC (`postgresql.conf`
или `PGTZ`), перезапустить backend и только потом чинить партиции; пересоздание
уже смещённых партиций — отдельная ручная операция, не делайте её вслепую.
