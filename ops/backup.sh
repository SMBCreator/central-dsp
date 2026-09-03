#!/usr/bin/env bash
# Ежедневный бэкап CentralDSP на VPS (docker-compose стек).
#
#   pg_dump (custom-формат, сжатие) -> $BACKUP_DIR/pg/dsp-YYYY-MM-DD_HHMM.dump
#   redis BGSAVE -> копия dump.rdb (счётчики бюджетов/капов; допустимо потерять)
#   ротация: хранить RETENTION_DAYS дней
#   проверка: раз в неделю (воскресенье) или при VERIFY=1 — pg_restore свежего
#             дампа во временную БД, SELECT count(*) по ключевым таблицам, DROP.
#
# Целевые показатели: RPO <= 24 ч (ежедневно 03:30 UTC), RTO <= 30 мин
# (ops/restore.sh). Финансовый источник истины — таблица ledger в Postgres;
# Redis восстанавливать не нужно.
#
# Запуск из cron (см. docs/DEPLOYMENT.md §9):
#   30 3 * * * root /opt/centraldsp/ops/backup.sh >> /var/log/centraldsp-backup.log 2>&1
#
# Переменные (можно задать в /etc/default/centraldsp-backup):
#   COMPOSE_DIR   каталог с docker-compose.yml           (default /opt/centraldsp)
#   BACKUP_DIR    куда складывать                        (default /backup/centraldsp)
#   RETENTION_DAYS                                        (default 14)
#   OFFSITE_CMD   команда, получающая путь файла $1 для копии вне хоста, например:
#                 'rclone copy "$1" s3:centraldsp-backups/pg/'  (default пусто)
#   ALERT_CMD     команда для уведомления об ошибке, получает текст $1 (default пусто)
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/centraldsp}"
BACKUP_DIR="${BACKUP_DIR:-/backup/centraldsp}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
OFFSITE_CMD="${OFFSITE_CMD:-}"
ALERT_CMD="${ALERT_CMD:-}"
VERIFY="${VERIFY:-auto}"

[ -f /etc/default/centraldsp-backup ] && . /etc/default/centraldsp-backup

cd "$COMPOSE_DIR"
# .env стека: POSTGRES_DB / POSTGRES_USER
[ -f .env ] && set -a && . ./.env && set +a
PG_DB="${POSTGRES_DB:-centraldsp}"
PG_USER="${POSTGRES_USER:-centraldsp}"

STAMP="$(date -u +%F_%H%M)"
PG_OUT="$BACKUP_DIR/pg/dsp-$STAMP.dump"
REDIS_OUT="$BACKUP_DIR/redis/dump-$STAMP.rdb"
mkdir -p "$BACKUP_DIR/pg" "$BACKUP_DIR/redis"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
fail() {
  log "ERROR: $*"
  if [ -n "$ALERT_CMD" ]; then bash -c "$ALERT_CMD" _ "centraldsp backup failed: $*" || true; fi
  exit 1
}
dc() { docker compose "$@"; }

# --- 1. Postgres -----------------------------------------------------------
log "pg_dump $PG_DB -> $PG_OUT"
# -Fc: custom-формат (pg_restore, выборочное восстановление, встроенное сжатие)
dc exec -T postgres pg_dump -U "$PG_USER" -Fc -Z 6 --no-owner --no-privileges "$PG_DB" > "$PG_OUT.tmp" \
  || fail "pg_dump exited non-zero"
# Пустой/битый файл ловим сразу: pg_restore -l читает TOC без восстановления.
dc exec -T postgres pg_restore -l < "$PG_OUT.tmp" > /dev/null 2>&1 || fail "dump TOC unreadable"
mv "$PG_OUT.tmp" "$PG_OUT"
sha256sum "$PG_OUT" > "$PG_OUT.sha256"
log "pg dump ok: $(du -h "$PG_OUT" | cut -f1)"

# --- 2. Redis (best-effort) ------------------------------------------------
if dc exec -T redis redis-cli BGSAVE >/dev/null 2>&1; then
  # Ждём завершения фонового снапшота (не дольше 60 с).
  for _ in $(seq 1 60); do
    if [ "$(dc exec -T redis redis-cli LASTSAVE)" != "${LASTSAVE_PREV:-}" ] && \
       [ "$(dc exec -T redis redis-cli INFO persistence | tr -d '\r' | awk -F: '/rdb_bgsave_in_progress/{print $2}')" = "0" ]; then
      break
    fi
    sleep 1
  done
  if docker compose cp redis:/data/dump.rdb "$REDIS_OUT" 2>/dev/null; then
    log "redis rdb ok: $(du -h "$REDIS_OUT" | cut -f1)"
  else
    log "WARN: redis dump.rdb copy failed (non-fatal)"
  fi
else
  log "WARN: redis BGSAVE failed (non-fatal)"
fi

# --- 3. Offsite -------------------------------------------------------------
if [ -n "$OFFSITE_CMD" ]; then
  bash -c "$OFFSITE_CMD" _ "$PG_OUT" || fail "offsite copy failed"
  bash -c "$OFFSITE_CMD" _ "$PG_OUT.sha256" || true
  log "offsite copy ok"
else
  log "WARN: OFFSITE_CMD not set — backup exists only on this host"
fi

# --- 4. Rotation ------------------------------------------------------------
find "$BACKUP_DIR/pg" -type f -name 'dsp-*.dump*' -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR/redis" -type f -name 'dump-*.rdb' -mtime +"$RETENTION_DAYS" -delete
log "rotation done (keep ${RETENTION_DAYS}d): $(ls "$BACKUP_DIR/pg"/*.dump 2>/dev/null | wc -l) pg dumps"

# --- 5. Restore check (weekly or forced) -----------------------------------
do_verify=0
case "$VERIFY" in
  1|true|yes) do_verify=1 ;;
  auto) [ "$(date -u +%u)" = "7" ] && do_verify=1 ;;
esac
if [ "$do_verify" = "1" ]; then
  TMP_DB="restore_check_$(date -u +%s)"
  log "verify: pg_restore -> $TMP_DB"
  dc exec -T postgres psql -U "$PG_USER" -d postgres -qc "CREATE DATABASE $TMP_DB" \
    || fail "verify: create temp db"
  if dc exec -T postgres pg_restore -U "$PG_USER" -d "$TMP_DB" --no-owner --no-privileges < "$PG_OUT"; then
    counts="$(dc exec -T postgres psql -U "$PG_USER" -d "$TMP_DB" -Atc \
      "SELECT 'campaigns='||count(*) FROM campaigns
       UNION ALL SELECT 'ledger='||count(*) FROM ledger
       UNION ALL SELECT 'supply_partners='||count(*) FROM supply_partners
       UNION ALL SELECT 'migrations='||count(*) FROM schema_migrations" 2>&1 | tr '\n' ' ')"
    log "verify ok: $counts"
    dc exec -T postgres psql -U "$PG_USER" -d postgres -qc "DROP DATABASE $TMP_DB"
  else
    dc exec -T postgres psql -U "$PG_USER" -d postgres -qc "DROP DATABASE IF EXISTS $TMP_DB" || true
    fail "verify: pg_restore failed"
  fi
fi

log "backup finished"
