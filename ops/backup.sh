#!/usr/bin/env bash
# Central DSP — резервная копия (C2 / audit O-02).
#
#   ops/backup.sh                 # PostgreSQL dump + uploads + manifest -> backup-<ts>.tar
#   ops/backup.sh --with-redis    # + redis/dump.rdb (recovery artifact; restore только явным флагом)
#
# Cron:  30 3 * * *  root  cd /opt/central-dsp && ops/backup.sh >> /var/log/centraldsp-backup.log 2>&1
#
# Один артефакт = один файл  $BACKUP_DIR/backup-<ts>.tar  (см. ops/lib/backup_common.sh,
# там же описан состав и SQL финансовой сверки). Только его принимает ops/restore.sh.
#
# Консистентность: pg_dump и контрольные снимки (ledger_snapshot.tsv, counts.tsv,
# schema_migrations.tsv) делаются из ОДНОГО снапшота Postgres (pg_export_snapshot +
# pg_dump --snapshot), поэтому restore сверяет базу с manifest байт в байт, а не
# «примерно». uploads.tar снимается ПОСЛЕ дампа: лишний файл без строки в БД
# безобиден, строка creatives без файла — нет.
#
# Переменные (/etc/default/centraldsp-backup или окружение cron):
#   COMPOSE_DIR      каталог с compose.yml (default: корень репозитория = ../ от ops/)
#   BACKUP_DIR       куда писать артефакты (default /var/backups/centraldsp)
#   RETENTION_DAYS   сколько дней хранить backup-*.tar локально (default 14)
#   POSTGRES_USER / POSTGRES_DB   как в .env (default centraldsp / centraldsp)
#   PG_DUMP_COMPRESSION  уровень -Z для pg_dump (default 6)
#   VERIFY           never | weekly (по воскресеньям) | always — restore.sh --scratch
#                    на свежий артефакт: реальный restore в временную БД + финансовая сверка
#   OFFSITE_CMD      команда, получающая путь к .tar последним аргументом
#                    (например: OFFSITE_CMD="rclone copyto --s3-... " или свой скрипт).
#                    Ничего провайдер-специфичного здесь нет намеренно: S3/Object Lock — отдельная задача.
#   ALERT_CMD        команда, получающая текст ошибки первым аргументом
#   HELPER_IMAGE     образ для tar volume'ов (default alpine:3.20)
#   REDIS_BGSAVE_TIMEOUT  сек ожидания BGSAVE (default 180)
#
# Коды выхода: 0 ok; 2 usage/env; 3 dump/snapshot failed; 4 VERIFY failed (артефакт сохранён);
#              5 OFFSITE_CMD failed (артефакт сохранён локально); 6 redis artifact failed.
set -Eeuo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib/backup_common.sh
. "$OPS_DIR/lib/backup_common.sh"

COMPOSE_DIR="${COMPOSE_DIR:-$(cd "$OPS_DIR/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/centraldsp}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
PG_DUMP_COMPRESSION="${PG_DUMP_COMPRESSION:-6}"
VERIFY="${VERIFY:-weekly}"
OFFSITE_CMD="${OFFSITE_CMD:-}"
ALERT_CMD="${ALERT_CMD:-}"
HELPER_IMAGE="${HELPER_IMAGE:-alpine:3.20}"
REDIS_BGSAVE_TIMEOUT="${REDIS_BGSAVE_TIMEOUT:-180}"

WITH_REDIS=0
for arg in "$@"; do
  case "$arg" in
    --with-redis) WITH_REDIS=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) die "unknown argument: $arg (see --help)" 2 ;;
  esac
done

need_cmd docker jq tar sha256sum awk find
cd "$COMPOSE_DIR" || die "COMPOSE_DIR not found: $COMPOSE_DIR" 2
[ -f compose.yml ] || die "compose.yml not found in $COMPOSE_DIR" 2

# .env стека даёт POSTGRES_*; уже экспортированные переменные имеют приоритет.
if [ -f .env ]; then
  while IFS='=' read -r k v; do
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    case "$k" in POSTGRES_USER|POSTGRES_DB) [ -z "${!k:-}" ] && export "$k=$v" ;; esac
  done < <(grep -E '^(POSTGRES_USER|POSTGRES_DB)=' .env || true)
fi
PG_USER="${POSTGRES_USER:-centraldsp}"
PG_DB="${POSTGRES_DB:-centraldsp}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="backup-$STAMP"
OUT="$BACKUP_DIR/$NAME"
TAR="$BACKUP_DIR/$NAME.tar"
WORK="/tmp/cdsp_$NAME"          # рабочий каталог ВНУТРИ контейнера postgres

alert() { [ -n "$ALERT_CMD" ] && $ALERT_CMD "centraldsp backup: $*" || true; }
cleanup() {
  local rc=$?
  dc exec -T "$PG_SERVICE" rm -rf "$WORK" >/dev/null 2>&1 || true
  rm -f "${SUMS_TMP:-}" 2>/dev/null || true
  if [ $rc -ne 0 ]; then
    log "backup FAILED (exit $rc)"; alert "FAILED exit=$rc stamp=$STAMP"
    [ -d "$OUT" ] && [ ! -f "$TAR" ] && rm -rf "$OUT"
  fi
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"
[ -e "$OUT" ] && die "output already exists: $OUT" 2
mkdir -p "$OUT"
log "backup start: db=$PG_DB user=$PG_USER out=$TAR with_redis=$WITH_REDIS"

service_running "$PG_SERVICE" || die "service '$PG_SERVICE' is not running (docker compose ps)" 2

# ── 1. PostgreSQL: снапшот -> pg_dump --snapshot -> контрольные снимки -> TOC ──
dc exec -T "$PG_SERVICE" mkdir -p "$WORK"
if ! dc exec -T -e PGUSER="$PG_USER" -e PGDATABASE="$PG_DB" "$PG_SERVICE" \
     psql -v ON_ERROR_STOP=1 -X -q -At -F "$(printf '\t')" <<EOSQL
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
\o $WORK/snapshot.txt
SELECT pg_export_snapshot();
\o
\! pg_dump -Fc -Z $PG_DUMP_COMPRESSION --no-owner --no-privileges --snapshot="\$(cat $WORK/snapshot.txt)" -f $WORK/postgres.dump && touch $WORK/dump.ok
\o $WORK/ledger_snapshot.tsv
$LEDGER_SNAPSHOT_SQL;
\o $WORK/schema_migrations.tsv
$SCHEMA_SNAPSHOT_SQL;
\o $WORK/counts.tsv
$COUNTS_SNAPSHOT_SQL;
\o
COMMIT;
\! pg_restore -l $WORK/postgres.dump > $WORK/pg_restore_toc.txt && touch $WORK/toc.ok
EOSQL
then
  die "psql/pg_dump session failed" 3
fi
dc exec -T "$PG_SERVICE" test -f "$WORK/dump.ok" || die "pg_dump did not finish (no dump.ok)" 3
dc exec -T "$PG_SERVICE" test -f "$WORK/toc.ok"  || die "pg_restore -l failed: dump is not readable" 3
dc cp "$PG_SERVICE:$WORK/." "$OUT/"
rm -f "$OUT/snapshot.txt" "$OUT/dump.ok" "$OUT/toc.ok"
for f in postgres.dump ledger_snapshot.tsv schema_migrations.tsv counts.tsv pg_restore_toc.txt; do
  [ -s "$OUT/$f" ] || { [ "$f" = ledger_snapshot.tsv ] && [ -f "$OUT/$f" ]; } || die "missing or empty $f after copy" 3
done
log "postgres.dump: $(du -h "$OUT/postgres.dump" | cut -f1), toc entries: $(grep -cv '^;' "$OUT/pg_restore_toc.txt" || true)"

# ── 2. uploads (после дампа — см. шапку) ──
UPLOADS_VOL="$(compose_volume_name uploads_data || true)"
if [ -n "$UPLOADS_VOL" ] && docker_volume_exists "$UPLOADS_VOL"; then
  docker run --rm -v "$UPLOADS_VOL:/src:ro" -v "$OUT:/out" "$HELPER_IMAGE" \
    tar -C /src -cf /out/uploads.tar .
  UPLOADS_FILES="$(tar -tf "$OUT/uploads.tar" | grep -vc '/$' || true)"
  log "uploads.tar: $UPLOADS_FILES files from volume $UPLOADS_VOL"
else
  warn "uploads volume not found (${UPLOADS_VOL:-uploads_data}); uploads.tar skipped"
  UPLOADS_FILES=""
fi

# ── 3. Redis (только --with-redis): BGSAVE -> dump.rdb. Default — НЕ бэкапим: ──
#    в Redis живут locks, резервы, rate limits, pending BURL; их «воскрешение»
#    из старой копии опаснее, чем пересчёт из Postgres (см. restore.sh).
REDIS_JSON=null
if [ $WITH_REDIS -eq 1 ]; then
  service_running "$REDIS_SERVICE" || die "service '$REDIS_SERVICE' is not running" 6
  redis_cli() { dc exec -T "$REDIS_SERVICE" redis-cli "$@" | tr -d '\r'; }
  redis_info() { redis_cli INFO persistence | awk -F: -v k="$1" '$1 == k { print $2 }'; }
  START_TS="$(date +%s)"
  reply="$(redis_cli BGSAVE)"
  case "$reply" in
    Background*) ;;
    *) die "BGSAVE refused: $reply" 6 ;;
  esac
  deadline=$((START_TS + REDIS_BGSAVE_TIMEOUT))
  while :; do
    inprog="$(redis_info rdb_bgsave_in_progress)"; lastsave="$(redis_info rdb_last_save_time)"
    if [ "${inprog:-1}" = "0" ] && [ "${lastsave:-0}" -ge "$((START_TS - 1))" ]; then break; fi
    [ "$(date +%s)" -lt "$deadline" ] || die "BGSAVE did not finish in ${REDIS_BGSAVE_TIMEOUT}s" 6
    sleep 1
  done
  [ "$(redis_info rdb_last_bgsave_status)" = "ok" ] || die "rdb_last_bgsave_status != ok" 6
  mkdir -p "$OUT/redis"
  dc cp "$REDIS_SERVICE:/data/dump.rdb" "$OUT/redis/dump.rdb"
  [ -s "$OUT/redis/dump.rdb" ] || die "redis dump.rdb is empty" 6
  REDIS_DBSIZE="$(redis_cli DBSIZE)"
  REDIS_AOF_FILES="$(dc exec -T "$REDIS_SERVICE" sh -c 'ls -1 /data/appendonlydir 2>/dev/null || true' | tr -d '\r' | jq -R . | jq -s .)"
  REDIS_JSON="$(jq -n \
    --arg mode "aof:$(redis_info aof_enabled) rdb_save_disabled" \
    --arg aof_enabled "$(redis_info aof_enabled)" \
    --arg captured_at "$(date -u -d "@$lastsave" +%FT%TZ 2>/dev/null || date -u +%FT%TZ)" \
    --argjson dbsize "${REDIS_DBSIZE:-0}" \
    --argjson aof_files "$REDIS_AOF_FILES" \
    --arg version "$(redis_cli INFO server | awk -F: '$1=="redis_version"{print $2}')" \
    '{persistence_mode: $mode, aof_enabled: ($aof_enabled == "1"), rdb_captured_at: $captured_at,
      dbsize: $dbsize, aof_files_at_capture: $aof_files, redis_version: $version, artifact: "redis/dump.rdb",
      restore_requires: "--with-redis --yes-replace-redis"}')"
  log "redis/dump.rdb: dbsize=$REDIS_DBSIZE, aof files at capture: $(echo "$REDIS_AOF_FILES" | jq length)"
fi

# ── 4. manifest.json ──
PG_VERSION="$(pg_sql "$PG_DB" 'SHOW server_version' | tr -d '\r')"
OK_N="$(ledger_status_count "$OUT/ledger_snapshot.tsv" ok)"
ROUND_N="$(ledger_status_count "$OUT/ledger_snapshot.tsv" rounding)"
MISMATCH_N="$(ledger_status_count "$OUT/ledger_snapshot.tsv" mismatch)"
RECON_STATUS=ok; [ "$ROUND_N" -gt 0 ] && RECON_STATUS=rounding_only; [ "$MISMATCH_N" -gt 0 ] && RECON_STATUS=mismatch
if [ "$MISMATCH_N" -gt 0 ]; then
  warn "ledger reconciliation: $MISMATCH_N advertiser(s) with unexplained balance != sum(transactions) at backup time:"
  awk -F'\t' '$7 == "mismatch" { printf "    %s balance=%s ledger_sum=%s delta=%s\n", $1, $2, $3, $6 }' "$OUT/ledger_snapshot.tsv"
  alert "ledger mismatch for $MISMATCH_N advertiser(s) in $NAME (backup still written)"
fi

# список sha256 пишем ВНЕ $OUT: файл, созданный редиректом внутри $OUT, попал бы в find
SUMS_TMP="$(mktemp "${TMPDIR:-/tmp}/cdsp_sums_XXXXXX")"
( cd "$OUT" && find . -type f ! -name manifest.json ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum ) > "$SUMS_TMP"
jq -n \
  --argjson format "$BACKUP_FORMAT_VERSION" \
  --arg name "$NAME" --arg created "$(date -u +%FT%TZ)" \
  --arg host "$(uname -n)" --arg compose_project "$(dc config --format json | jq -r .name)" \
  --arg db "$PG_DB" --arg pg_version "$PG_VERSION" \
  --arg git "$(git -C "$COMPOSE_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  --argjson counts "$(jq -Rn '[inputs | split("\t") | {(.[0]): .[1]}] | add // {}' "$OUT/counts.tsv")" \
  --argjson schema_count "$(wc -l < "$OUT/schema_migrations.tsv")" \
  --arg schema_last "$(tail -n1 "$OUT/schema_migrations.tsv" | cut -f1)" \
  --argjson ok "$OK_N" --argjson rounding "$ROUND_N" --argjson mismatch "$MISMATCH_N" --arg recon "$RECON_STATUS" \
  --argjson uploads "$( [ -n "$UPLOADS_FILES" ] && jq -n --arg v "$UPLOADS_VOL" --argjson n "$UPLOADS_FILES" '{volume: $v, files: $n, artifact: "uploads.tar"}' || echo null )" \
  --argjson redis "$REDIS_JSON" \
  --argjson files "$(jq -Rn '[inputs | capture("^(?<sha>[0-9a-f]{64})  \\./(?<f>.+)$") | {(.f): .sha}] | add // {}' "$SUMS_TMP")" \
  '{
     format_version: $format, name: $name, created_at: $created, host: $host,
     compose_project: $compose_project, git_commit: $git,
     postgres: { database: $db, server_version: $pg_version, dump: "postgres.dump", dump_format: "custom",
                 snapshot_consistent: true },
     schema: { migrations_applied: $schema_count, last_migration: $schema_last, snapshot: "schema_migrations.tsv" },
     counts: $counts,
     reconciliation: {
       rule: "advertisers.balance vs SUM(transactions.amount) per advertiser; rounding tolerance spend_count*0.00005 (NUMERIC(14,4) balance vs NUMERIC(14,6) amount)",
       status: $recon, advertisers_ok: $ok, advertisers_rounding: $rounding, advertisers_mismatch: $mismatch,
       snapshot: "ledger_snapshot.tsv",
       restore_criterion: "restored ledger_snapshot/counts/schema_migrations must equal the artifact byte-for-byte"
     },
     uploads: $uploads, redis: $redis, sha256: $files
   }' > "$OUT/manifest.json"
rm -f "$SUMS_TMP"
( cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )

# ── 5. один файл-артефакт + ротация ──
tar -C "$BACKUP_DIR" -cf "$TAR.part" "$NAME" && mv "$TAR.part" "$TAR"
rm -rf "$OUT"
log "artifact: $TAR ($(du -h "$TAR" | cut -f1)) advertisers=$(counts_get <(tar -xOf "$TAR" "$NAME/counts.tsv") advertisers) transactions=$(counts_get <(tar -xOf "$TAR" "$NAME/counts.tsv") transactions) reconciliation=$RECON_STATUS"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'backup-*.tar' -mtime "+$RETENTION_DAYS" -print -delete | sed 's/^/rotated: /' || true
# артефакты прежнего формата (каталоги <YYYYmmdd_HHMMSS>/) ротируем по тому же сроку
find "$BACKUP_DIR" -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9]{8}_[0-9]{6}' -mtime "+$RETENTION_DAYS" -print -exec rm -rf {} + | sed 's/^/rotated legacy: /' || true

# ── 6. VERIFY: настоящий restore во временную БД + финансовая сверка ──
do_verify=0
case "$VERIFY" in
  always) do_verify=1 ;;
  weekly) [ "$(date -u +%u)" = "7" ] && do_verify=1 ;;
  never|"") ;;
  *) warn "VERIFY=$VERIFY unknown, treating as never" ;;
esac
VERIFY_RC=0
if [ $do_verify -eq 1 ]; then
  log "VERIFY: ops/restore.sh --scratch"
  if COMPOSE_DIR="$COMPOSE_DIR" POSTGRES_USER="$PG_USER" "$OPS_DIR/restore.sh" "$TAR" --scratch; then
    log "VERIFY passed"
  else
    VERIFY_RC=$?; alert "VERIFY (scratch restore + reconciliation) FAILED for $NAME rc=$VERIFY_RC"
    log "VERIFY FAILED rc=$VERIFY_RC — artifact kept: $TAR"
  fi
fi

# ── 7. offsite hook ──
OFFSITE_RC=0
if [ -n "$OFFSITE_CMD" ]; then
  log "offsite: $OFFSITE_CMD $TAR"
  # shellcheck disable=SC2086
  if $OFFSITE_CMD "$TAR"; then log "offsite ok"; else OFFSITE_RC=$?; alert "OFFSITE_CMD failed rc=$OFFSITE_RC for $TAR"; log "offsite FAILED rc=$OFFSITE_RC (local artifact kept)"; fi
else
  warn "OFFSITE_CMD not set — copy $TAR off this host yourself; a backup on the same VPS does not survive losing the VPS"
fi

log "backup completed: $TAR"
[ $VERIFY_RC -ne 0 ] && exit 4
[ $OFFSITE_RC -ne 0 ] && exit 5
exit 0
