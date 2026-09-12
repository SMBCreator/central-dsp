#!/usr/bin/env bash
# Общая часть ops/backup.sh, ops/restore.sh и ops/tests/backup_restore_drill.sh (C2 / O-02).
#
# Здесь живут: логирование, обёртки над `docker compose exec postgres ...`,
# и — главное — ЕДИНЫЕ SQL-снимки, по которым backup пишет manifest, а restore
# сверяет восстановленную базу. Один источник SQL => backup и restore не могут
# разойтись в определении «что такое сверка».
#
# Артефакт бэкапа (backup-<ts>.tar), внутри каталог backup-<ts>/:
#   postgres.dump           pg_dump -Fc (custom, seekable — pg_restore --jobs)
#   ledger_snapshot.tsv     по одному ряду на рекламодателя (см. LEDGER_SNAPSHOT_SQL)
#   schema_migrations.tsv   filename / sha256 / stamped
#   counts.tsv              контрольные счётчики и суммы
#   uploads.tar             содержимое volume uploads_data (если volume есть)
#   redis/dump.rdb          только с --with-redis (recovery artifact, не default)
#   manifest.json           агрегаты + sha256 + версия схемы + режим Redis
#   SHA256SUMS              sha256 всех файлов выше
#
# Все три .tsv снимаются В ТОЙ ЖЕ транзакции/снапшоте, что и pg_dump
# (pg_export_snapshot + pg_dump --snapshot), поэтому после restore они должны
# совпасть с восстановленной базой байт в байт — это и есть критерий
# «verification passed».

# shellcheck disable=SC2034  # переменные используются в подключающих скриптах
BACKUP_FORMAT_VERSION=2

log()  { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
warn() { log "WARN: $*"; }
# die <msg> [exit-code]
die()  { log "ERROR: $1"; exit "${2:-1}"; }

need_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "required command not found: $c" 2
  done
}

dc() { docker compose "$@"; }

PG_SERVICE="${PG_SERVICE:-postgres}"
REDIS_SERVICE="${REDIS_SERVICE:-redis}"

# psql к базе $1 внутри контейнера postgres; остальные аргументы — psql.
# Всегда: ON_ERROR_STOP, без выравнивания/заголовков (-At), TAB как разделитель.
pg_psql() {
  local db="$1"; shift
  dc exec -T "$PG_SERVICE" psql -v ON_ERROR_STOP=1 -X -q -At -F "$(printf '\t')" -U "$PG_USER" -d "$db" "$@"
}

# Одиночный запрос к базе $1: pg_sql <db> <sql>
pg_sql() { pg_psql "$1" -c "$2"; }

# Имя docker volume по ключу из compose (uploads_data, redis_data, ...):
# учитывает COMPOSE_PROJECT_NAME/name:, поэтому не зашиваем «centraldsp_».
compose_volume_name() {
  dc config --format json 2>/dev/null | jq -r --arg v "$1" '.volumes[$v].name // empty'
}

docker_volume_exists() { docker volume inspect "$1" >/dev/null 2>&1; }

# Контейнер сервиса $1 существует и запущен (без `compose ps --status`, которого
# нет в старых compose v2).
service_running() {
  local cid
  cid="$(dc ps -q "$1" 2>/dev/null | head -n1)"
  [ -n "$cid" ] && [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" = "true" ]
}

# Дождаться healthy/running контейнера сервиса $1 (таймаут $2 сек, default 120).
wait_service_healthy() {
  local svc="$1" timeout="${2:-120}" cid st deadline
  deadline=$(( $(date +%s) + timeout ))
  while :; do
    cid="$(dc ps -q "$svc" 2>/dev/null | head -n1)"
    if [ -n "$cid" ]; then
      st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null)"
      case "$st" in healthy|running) return 0 ;; esac
    fi
    [ "$(date +%s)" -lt "$deadline" ] || return 1
    sleep 2
  done
}

# ---------------------------------------------------------------------------
# Финансовая сверка. Модель денег (billing_core.bill_win, routers/billing._money_op):
#   * advertisers.balance меняется ТОЛЬКО вместе с INSERT в transactions
#     (одна транзакция БД): spend -> amount = -cost, deposit/adjustment -> amount = ±сумма;
#   * transactions — append-only (триггер trg_transactions_append_only);
#   * резервы (res:adv/res:camp) живут в Redis и в balance НЕ входят;
#   * refund/credit limit/FX в модели нет (типы: deposit | spend | adjustment).
# Значит для каждого рекламодателя  balance ≈ SUM(transactions.amount).
# «≈», а не «=»: amount хранится NUMERIC(14,6) (money.py квантует cost до 6 знаков),
# balance — NUMERIC(14,4); каждое spend-списание округляет balance до 4 знаков,
# поэтому допустимый дрейф = spend_count * 0.00005 (половина 0.0001 на строку).
# Проверено на живой схеме: 200 spend'ов -> balance 94.6886 vs ledger 94.688000.
#   status: ok        — равенство точное
#           rounding  — |delta| в пределах spend_count*0.00005 (модельное округление)
#           mismatch  — необъяснённая разница (ручной seed balance без проводки и т.п.)
# Для restore критерий не «status == ok», а «снимок после restore == снимок в
# артефакте» построчно: pre-existing mismatch виден в manifest и не валит
# восстановление, любое новое расхождение — валит (exit 4).
# ---------------------------------------------------------------------------
read -r -d '' LEDGER_SNAPSHOT_SQL <<'SQL' || true
WITH l AS (
  SELECT advertiser_id,
         SUM(amount)                            AS ledger_sum,
         COUNT(*)                               AS tx_count,
         COUNT(*) FILTER (WHERE type = 'spend') AS spend_count
  FROM transactions
  WHERE advertiser_id IS NOT NULL
  GROUP BY advertiser_id
)
SELECT a.id,
       a.balance::numeric(16,4)::text                                  AS balance,
       COALESCE(l.ledger_sum, 0)::numeric(16,6)::text                  AS ledger_sum,
       COALESCE(l.tx_count, 0)                                         AS tx_count,
       COALESCE(l.spend_count, 0)                                      AS spend_count,
       (a.balance - COALESCE(l.ledger_sum, 0))::numeric(16,6)::text    AS delta,
       CASE
         WHEN a.balance = COALESCE(l.ledger_sum, 0) THEN 'ok'
         WHEN abs(a.balance - COALESCE(l.ledger_sum, 0)) <= COALESCE(l.spend_count, 0) * 0.00005 THEN 'rounding'
         ELSE 'mismatch'
       END                                                              AS status
FROM advertisers a
LEFT JOIN l ON l.advertiser_id = a.id
ORDER BY a.id
SQL

read -r -d '' SCHEMA_SNAPSHOT_SQL <<'SQL' || true
SELECT filename, sha256, stamped::text
FROM schema_migrations
ORDER BY filename
SQL

# Контрольные счётчики. ORDER BY по порядковому номеру — чтобы diff был стабильным.
read -r -d '' COUNTS_SNAPSHOT_SQL <<'SQL' || true
SELECT k, v FROM (
  SELECT  1 AS n, 'advertisers'               AS k, (SELECT count(*) FROM advertisers)::text AS v
  UNION ALL SELECT  2, 'balance_total',            (SELECT COALESCE(SUM(balance), 0)::numeric(18,4) FROM advertisers)::text
  UNION ALL SELECT  3, 'transactions',             (SELECT count(*) FROM transactions)::text
  UNION ALL SELECT  4, 'transactions_total',       (SELECT COALESCE(SUM(amount), 0)::numeric(18,6) FROM transactions)::text
  UNION ALL SELECT  5, 'transactions_orphan',      (SELECT count(*) FROM transactions WHERE advertiser_id IS NULL)::text
  UNION ALL SELECT  6, 'transactions_orphan_total',(SELECT COALESCE(SUM(amount), 0)::numeric(18,6) FROM transactions WHERE advertiser_id IS NULL)::text
  UNION ALL SELECT  7, 'transactions_max_id',      (SELECT COALESCE(max(id), 0) FROM transactions)::text
  UNION ALL SELECT  8, 'users',                    (SELECT count(*) FROM users)::text
  UNION ALL SELECT  9, 'campaigns',                (SELECT count(*) FROM campaigns)::text
  UNION ALL SELECT 10, 'line_items',               (SELECT count(*) FROM line_items)::text
  UNION ALL SELECT 11, 'creatives',                (SELECT count(*) FROM creatives)::text
  UNION ALL SELECT 12, 'supply_partners',          (SELECT count(*) FROM supply_partners)::text
  UNION ALL SELECT 13, 'bid_events',               (SELECT count(*) FROM bid_events)::text
  UNION ALL SELECT 14, 'schema_migrations',        (SELECT count(*) FROM schema_migrations)::text
  UNION ALL SELECT 15, 'schema_last_migration',    (SELECT COALESCE(max(filename), '') FROM schema_migrations)
) t ORDER BY n
SQL

# Снять все три .tsv из базы $1 в каталог $2 (вне снапшота дампа — для restore-verify).
snapshot_to_dir() {
  local db="$1" dir="$2"
  mkdir -p "$dir"
  pg_psql "$db" -c "$LEDGER_SNAPSHOT_SQL"  > "$dir/ledger_snapshot.tsv"
  pg_psql "$db" -c "$SCHEMA_SNAPSHOT_SQL"  > "$dir/schema_migrations.tsv"
  pg_psql "$db" -c "$COUNTS_SNAPSHOT_SQL"  > "$dir/counts.tsv"
}

# counts.tsv -> значение по ключу
counts_get() { awk -F'\t' -v k="$2" '$1 == k { print $2; exit }' "$1"; }

# ledger_snapshot.tsv -> число строк со статусом $2
ledger_status_count() { awk -F'\t' -v s="$2" '$7 == s { n++ } END { print n + 0 }' "$1"; }

# Отчёт по расхождениям двух TSV с ключом в 1-й колонке (counts.tsv, schema_migrations.tsv):
# только отличающиеся/отсутствующие ключи, порядок — как в expected.
tsv_diff_report() {
  local expected="$1" actual="$2"
  awk -F'\t' '
    NR == FNR { order[++n] = $1; want[$1] = $0; next }
    { have[$1] = $0 }
    END {
      for (i = 1; i <= n; i++) {
        k = order[i]
        if (!(k in have))            printf "  - missing in restored db: %s\n", want[k]
        else if (want[k] != have[k]) printf "  ~ %s\n      expected: %s\n      actual:   %s\n", k, substr(want[k], length(k) + 2), substr(have[k], length(k) + 2)
      }
      for (k in have) if (!(k in want)) printf "  + only in restored db : %s\n", have[k]
    }
  ' "$expected" "$actual"
}

# Человекочитаемый отчёт по расхождениям двух ledger_snapshot.tsv:
# expected (артефакт) vs actual (восстановленная база), только отличающиеся строки.
ledger_diff_report() {
  local expected="$1" actual="$2"
  awk -F'\t' '
    NR == FNR { want[$1] = $0; next }
    {
      have[$1] = $0
      if (!($1 in want)) { printf "  + only in restored db : %s balance=%s ledger_sum=%s tx=%s\n", $1, $2, $3, $4; next }
      if (want[$1] != $0) {
        split(want[$1], e, "\t")
        printf "  ~ %s\n      expected: balance=%s ledger_sum=%s tx=%s spend=%s delta=%s status=%s\n      actual:   balance=%s ledger_sum=%s tx=%s spend=%s delta=%s status=%s\n", \
          $1, e[2], e[3], e[4], e[5], e[6], e[7], $2, $3, $4, $5, $6, $7
      }
    }
    END {
      for (id in want) if (!(id in have)) {
        split(want[id], e, "\t")
        printf "  - missing in restored db: %s balance=%s ledger_sum=%s tx=%s\n", id, e[2], e[3], e[4]
      }
    }
  ' "$expected" "$actual"
}
