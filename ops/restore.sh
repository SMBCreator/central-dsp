#!/usr/bin/env bash
# Central DSP — восстановление из артефакта ops/backup.sh (C2 / audit O-02).
#
#   ops/restore.sh <backup-<ts>.tar>                       # в НОВУЮ базу <db>_restore_<ts>, сверка, база остаётся
#   ops/restore.sh <tar> --scratch                         # во временную базу, сверка, DROP (учения / VERIFY из backup.sh)
#   ops/restore.sh <tar> --target-db centraldsp --yes      # боевое: DROP+CREATE базы, restore, сверка
#   ops/restore.sh <tar> --verify-only --target-db NAME    # только сверка существующей базы с артефактом
#   ops/restore.sh <tar> --extract-uploads DIR             # распаковать uploads.tar в DIR (ничего не трогает)
#   ... --restore-uploads --yes                            # заменить содержимое volume uploads_data (backend остановлен)
#   ... --with-redis --yes-replace-redis                   # заменить Redis из redis/dump.rdb (см. ниже)
#   ... --jobs N                                           # pg_restore --jobs (default 4)
#
# Принимает ТОЛЬКО backup-<ts>.tar формата 2 (manifest.json + SHA256SUMS). Порядок:
#   1. распаковка -> sha256sum -c SHA256SUMS: не сошлось -> exit 5, к базе не прикасаемся;
#   2. pg_restore --jobs из ФАЙЛА внутри контейнера (custom format, не stdin);
#   3. «restore completed» — данные в базе;
#   4. verification: из восстановленной базы снимаются те же ledger_snapshot / counts /
#      schema_migrations (SQL из ops/lib/backup_common.sh) и сравниваются с артефактом
#      байт в байт. Любое расхождение -> «verification FAILED», exit 4, база НЕ правится,
#      проводки НЕ дописываются, rollback НЕ делается — разбирается человек.
#      Restore без «verification passed» успешным НЕ считается.
#   5. uploads / Redis — только по явным флагам и только после успешной сверки.
#
# Redis по умолчанию НЕ восстанавливается: в нём locks, резервы res:*, rate limits,
# pending BURL, rtb:events — старая копия воскресит уже отработанные события.
# Источник истины — Postgres: после переключения DATABASE_URL выполните
#   docker compose exec backend python scripts/redis_rebuild_spend.py --apply
#   curl -H "Authorization: Bearer $CRON_SECRET" http://127.0.0.1:8000/cron/reconcile-reserves
# --with-redis --yes-replace-redis нужен только для полного DR «как было в момент
# BGSAVE». Так как Redis работает с AOF (appendonly yes) и при старте читает AOF, а не
# dump.rdb, подменять один dump.rdb нельзя. Делается: stop redis -> очистить volume ->
# положить dump.rdb -> поднять одноразовый redis-server с appendonly no (грузит RDB) ->
# CONFIG SET appendonly yes (переписывает AOF из загруженных данных) -> SHUTDOWN ->
# docker compose start redis -> DBSIZE == manifest.redis.dbsize, иначе exit 7.
#
# Переменные: COMPOSE_DIR, POSTGRES_USER, RESTORE_JOBS, HELPER_IMAGE (alpine:3.20),
#             RESTORE_TMP (default /tmp).
# Коды выхода: 0 restore+verification ok; 2 usage; 3 refused (guard); 4 verification FAILED;
#              5 checksum FAILED; 6 pg_restore FAILED; 7 redis FAILED; 8 uploads FAILED.
set -Eeuo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib/backup_common.sh
. "$OPS_DIR/lib/backup_common.sh"

COMPOSE_DIR="${COMPOSE_DIR:-$(cd "$OPS_DIR/.." && pwd)}"
HELPER_IMAGE="${HELPER_IMAGE:-alpine:3.20}"
RESTORE_TMP="${RESTORE_TMP:-/tmp}"
JOBS="${RESTORE_JOBS:-4}"

TAR="" TARGET="" YES=0 SCRATCH=0 VERIFY_ONLY=0 EXTRACT_UPLOADS="" RESTORE_UPLOADS=0 WITH_REDIS=0 YES_REDIS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --target-db) TARGET="${2:?--target-db NAME}"; shift 2 ;;
    --yes) YES=1; shift ;;
    --scratch) SCRATCH=1; shift ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    --extract-uploads) EXTRACT_UPLOADS="${2:?--extract-uploads DIR}"; shift 2 ;;
    --restore-uploads) RESTORE_UPLOADS=1; shift ;;
    --with-redis) WITH_REDIS=1; shift ;;
    --yes-replace-redis) YES_REDIS=1; shift ;;
    --jobs) JOBS="${2:?--jobs N}"; shift 2 ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    -*) die "unknown option: $1 (see --help)" 2 ;;
    *) [ -z "$TAR" ] || die "unexpected argument: $1" 2; TAR="$1"; shift ;;
  esac
done
[ -n "$TAR" ] || die "usage: ops/restore.sh <backup-<ts>.tar> [options] (see --help)" 2
[ -f "$TAR" ] || die "artifact not found: $TAR" 2
[[ "$JOBS" =~ ^[0-9]+$ ]] || die "--jobs must be a number" 2

need_cmd docker jq tar sha256sum awk cmp comm
cd "$COMPOSE_DIR" || die "COMPOSE_DIR not found: $COMPOSE_DIR" 2
[ -f compose.yml ] || die "compose.yml not found in $COMPOSE_DIR" 2
if [ -f .env ] && [ -z "${POSTGRES_USER:-}" ]; then
  POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | head -n1 | cut -d= -f2- | tr -d '"'"'" || true)"
fi
PG_USER="${POSTGRES_USER:-centraldsp}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# ── guards, до любых действий ──
if [ $VERIFY_ONLY -eq 1 ]; then
  [ -n "$TARGET" ] || die "--verify-only requires --target-db NAME" 2
  [ $SCRATCH -eq 0 ] && [ $RESTORE_UPLOADS -eq 0 ] && [ $WITH_REDIS -eq 0 ] || die "--verify-only cannot be combined with restore actions" 2
fi
[ -n "$TARGET" ] && [ $SCRATCH -eq 1 ] && die "--target-db and --scratch are mutually exclusive" 2
if [ $SCRATCH -eq 1 ]; then
  [ $RESTORE_UPLOADS -eq 0 ] && [ $WITH_REDIS -eq 0 ] && [ -z "$EXTRACT_UPLOADS" ] || die "--scratch only restores+verifies a temporary database; drop the uploads/redis flags" 2
fi
if [ -n "$TARGET" ]; then
  [[ "$TARGET" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die "--target-db: only [a-z_][a-z0-9_]* allowed" 2
  case "$TARGET" in postgres|template0|template1) die "refusing to touch system database $TARGET" 3 ;; esac
  [ $VERIFY_ONLY -eq 1 ] || [ $YES -eq 1 ] || die "--target-db $TARGET would DROP and recreate the database: add --yes to confirm" 3
fi
[ $RESTORE_UPLOADS -eq 0 ] || [ $YES -eq 1 ] || die "--restore-uploads replaces the uploads volume: add --yes to confirm" 3
[ $WITH_REDIS -eq 0 ] || [ $YES_REDIS -eq 1 ] || die "--with-redis replaces Redis state: add --yes-replace-redis to confirm" 3
[ $YES_REDIS -eq 0 ] || [ $WITH_REDIS -eq 1 ] || die "--yes-replace-redis without --with-redis" 2

WORKDIR="$(mktemp -d "$RESTORE_TMP/cdsp_restore_XXXXXX")"
CWORK="/tmp/cdsp_restore_$STAMP"       # внутри контейнера postgres
SCRATCH_DB=""
cleanup() {
  local rc=$?
  dc exec -T "$PG_SERVICE" rm -rf "$CWORK" >/dev/null 2>&1 || true
  if [ -n "$SCRATCH_DB" ]; then
    pg_sql postgres "DROP DATABASE IF EXISTS \"$SCRATCH_DB\" WITH (FORCE)" >/dev/null 2>&1 \
      && log "scratch database $SCRATCH_DB dropped" || warn "could not drop scratch database $SCRATCH_DB"
  fi
  rm -rf "$WORKDIR"
  [ $rc -eq 0 ] || log "restore.sh exit $rc"
}
trap cleanup EXIT

# ── 1. распаковка + checksum ──
tar -C "$WORKDIR" -xf "$TAR"
ART="$(find "$WORKDIR" -mindepth 1 -maxdepth 1 -type d -name 'backup-*' | head -n1)"
[ -n "$ART" ] || die "not a backup artifact: no backup-<ts>/ directory inside $TAR" 2
for f in manifest.json SHA256SUMS postgres.dump ledger_snapshot.tsv counts.tsv schema_migrations.tsv; do
  [ -f "$ART/$f" ] || die "artifact is missing $f (old format? only backup-<ts>.tar v$BACKUP_FORMAT_VERSION is accepted)" 2
done
FMT="$(jq -r '.format_version // 0' "$ART/manifest.json")"
[ "$FMT" = "$BACKUP_FORMAT_VERSION" ] || die "manifest format_version=$FMT, expected $BACKUP_FORMAT_VERSION" 2
if ! ( cd "$ART" && sha256sum -c --quiet SHA256SUMS ); then
  die "CHECKSUM FAILED: artifact $TAR is corrupted or tampered — nothing was restored" 5
fi
log "checksums ok: $(wc -l < "$ART/SHA256SUMS") files"

SRC_DB="$(jq -r .postgres.database "$ART/manifest.json")"
log "artifact: $(jq -r '.name' "$ART/manifest.json") created_at=$(jq -r .created_at "$ART/manifest.json") db=$SRC_DB pg=$(jq -r .postgres.server_version "$ART/manifest.json") git=$(jq -r .git_commit "$ART/manifest.json")"
log "manifest: advertisers=$(jq -r .counts.advertisers "$ART/manifest.json") balance_total=$(jq -r .counts.balance_total "$ART/manifest.json") transactions=$(jq -r .counts.transactions "$ART/manifest.json") transactions_total=$(jq -r .counts.transactions_total "$ART/manifest.json") bid_events=$(jq -r .counts.bid_events "$ART/manifest.json") schema=$(jq -r .schema.last_migration "$ART/manifest.json") ($(jq -r .schema.migrations_applied "$ART/manifest.json") applied)"
log "manifest reconciliation at backup time: status=$(jq -r .reconciliation.status "$ART/manifest.json") ok=$(jq -r .reconciliation.advertisers_ok "$ART/manifest.json") rounding=$(jq -r .reconciliation.advertisers_rounding "$ART/manifest.json") mismatch=$(jq -r .reconciliation.advertisers_mismatch "$ART/manifest.json")"

service_running "$PG_SERVICE" || die "service '$PG_SERVICE' is not running" 2

# ── предпроверки uploads/redis — ДО любых изменений в базе ──
if [ -n "$EXTRACT_UPLOADS" ] || [ $RESTORE_UPLOADS -eq 1 ]; then
  [ -f "$ART/uploads.tar" ] || die "artifact has no uploads.tar" 8
fi
UPLOADS_VOL=""
if [ $RESTORE_UPLOADS -eq 1 ]; then
  service_running backend && die "stop backend before --restore-uploads (docker compose stop backend)" 3
  UPLOADS_VOL="$(compose_volume_name uploads_data || true)"
  [ -n "$UPLOADS_VOL" ] || die "uploads_data volume is not defined in compose config" 8
fi
REDIS_VOL="" REDIS_IMAGE="" WANT_DBSIZE=""
if [ $WITH_REDIS -eq 1 ]; then
  [ "$(jq -r '.redis // "null"' "$ART/manifest.json")" != "null" ] && [ -f "$ART/redis/dump.rdb" ] \
    || die "artifact has no Redis recovery artifact (backup was made without --with-redis)" 7
  REDIS_VOL="$(compose_volume_name redis_data || true)"
  [ -n "$REDIS_VOL" ] || die "redis_data volume is not defined in compose config (managed Redis? restore it with the provider's tools)" 7
  REDIS_IMAGE="$(dc config --format json | jq -r ".services.\"$REDIS_SERVICE\".image // \"redis:7-alpine\"")"
  WANT_DBSIZE="$(jq -r .redis.dbsize "$ART/manifest.json")"
fi

# ── verification: снимок восстановленной базы == артефакт ──
verify_db() {
  local db="$1" actual="$WORKDIR/actual" fail=0 f
  snapshot_to_dir "$db" "$actual"
  log "verification of database $db against $(jq -r .name "$ART/manifest.json")"
  printf '  %-22s %-28s %-28s\n' metric expected actual
  for k in advertisers balance_total transactions transactions_total transactions_orphan bid_events campaigns creatives users schema_migrations schema_last_migration; do
    printf '  %-22s %-28s %-28s\n' "$k" "$(counts_get "$ART/counts.tsv" "$k")" "$(counts_get "$actual/counts.tsv" "$k")"
  done
  for f in counts.tsv schema_migrations.tsv ledger_snapshot.tsv; do
    if ! cmp -s "$ART/$f" "$actual/$f"; then
      fail=1
      log "MISMATCH in $f:"
      if [ "$f" = ledger_snapshot.tsv ]; then
        ledger_diff_report "$ART/$f" "$actual/$f"
      else
        tsv_diff_report "$ART/$f" "$actual/$f"
      fi
    fi
  done
  local m
  m="$(ledger_status_count "$actual/ledger_snapshot.tsv" mismatch)"
  if [ "$m" -gt 0 ]; then
    if [ "$m" = "$(jq -r .reconciliation.advertisers_mismatch "$ART/manifest.json")" ] && [ $fail -eq 0 ]; then
      log "note: $m advertiser(s) with balance != sum(transactions) — identical to backup time (pre-existing, explained by manifest):"
    else
      log "UNEXPLAINED: $m advertiser(s) with balance != sum(transactions) in restored db (manifest had $(jq -r .reconciliation.advertisers_mismatch "$ART/manifest.json")):"
    fi
    awk -F'\t' '$7 == "mismatch" { printf "    %s balance=%s ledger_sum=%s delta=%s\n", $1, $2, $3, $6 }' "$actual/ledger_snapshot.tsv"
  fi
  # схема vs код в репозитории — информационно (migrate.py накатит pending при старте backend)
  if [ -d "$COMPOSE_DIR/backend/migrations" ]; then
    local repo applied pending unknown
    repo="$(cd "$COMPOSE_DIR/backend/migrations" && ls -1 [0-9]*.sql 2>/dev/null | sort || true)"
    applied="$(cut -f1 "$actual/schema_migrations.tsv" | sort)"
    pending="$(comm -23 <(echo "$repo") <(echo "$applied") | tr '\n' ' ')"
    unknown="$(comm -13 <(echo "$repo") <(echo "$applied") | tr '\n' ' ')"
    [ -z "$pending" ] || warn "migrations in repo not applied in restored db (backend start will apply): $pending"
    [ -z "$unknown" ] || warn "migrations applied in restored db but absent in repo (code older than data?): $unknown"
  fi
  if [ $fail -eq 0 ]; then
    log "verification passed: ledger_snapshot, counts and schema_migrations match the artifact byte-for-byte"
    return 0
  fi
  log "verification FAILED: restored database differs from the artifact. Balances were NOT modified, no transactions were created, no rollback was performed — investigate before using this database."
  return 4
}

# ── --verify-only ──
if [ $VERIFY_ONLY -eq 1 ]; then
  [ "$(pg_sql postgres "SELECT 1 FROM pg_database WHERE datname = '$TARGET'")" = "1" ] || die "database $TARGET does not exist" 2
  verify_db "$TARGET" || exit $?
  exit 0
fi

# ── 2. целевая база ──
if [ $SCRATCH -eq 1 ]; then
  TARGET="restore_check_$(date -u +%Y%m%d%H%M%S)"; SCRATCH_DB="$TARGET"
elif [ -z "$TARGET" ]; then
  TARGET="${SRC_DB}_restore_$(date -u +%Y%m%d%H%M%S)"
fi
EXISTS="$(pg_sql postgres "SELECT 1 FROM pg_database WHERE datname = '$TARGET'")"
if [ "$EXISTS" = "1" ]; then
  [ $YES -eq 1 ] || die "database $TARGET already exists (use --target-db $TARGET --yes to replace it)" 3
  ACTIVE="$(pg_sql postgres "SELECT count(*) FROM pg_stat_activity WHERE datname = '$TARGET' AND pid <> pg_backend_pid()")"
  [ "${ACTIVE:-0}" = "0" ] || warn "$ACTIVE active connection(s) to $TARGET will be terminated — backend/scheduler/bidder-go should be stopped first"
  log "DROP DATABASE $TARGET (--yes)"
  pg_sql postgres "DROP DATABASE \"$TARGET\" WITH (FORCE)" >/dev/null
fi
pg_sql postgres "CREATE DATABASE \"$TARGET\" OWNER \"$PG_USER\"" >/dev/null
log "target database created: $TARGET"

# ── 3. pg_restore из файла внутри контейнера ──
dc exec -T "$PG_SERVICE" mkdir -p "$CWORK"
dc cp "$ART/postgres.dump" "$PG_SERVICE:$CWORK/postgres.dump"
log "pg_restore --jobs=$JOBS -> $TARGET"
if ! dc exec -T "$PG_SERVICE" pg_restore -U "$PG_USER" -d "$TARGET" --no-owner --no-privileges --jobs="$JOBS" "$CWORK/postgres.dump" 2> "$WORKDIR/pg_restore.err"; then
  sed 's/^/  pg_restore: /' "$WORKDIR/pg_restore.err" | tail -n 40
  die "pg_restore FAILED — database $TARGET is incomplete, do not use it" 6
fi
[ -s "$WORKDIR/pg_restore.err" ] && sed 's/^/  pg_restore: /' "$WORKDIR/pg_restore.err" | tail -n 20
dc exec -T "$PG_SERVICE" rm -rf "$CWORK"
log "restore completed: database=$TARGET (data is in place; NOT yet verified)"

# ── 4. verification ──
verify_db "$TARGET" || exit $?

if [ $SCRATCH -eq 1 ]; then
  log "scratch mode: verification passed, dropping $TARGET"
  exit 0
fi

# ── 5. uploads ──
if [ -n "$EXTRACT_UPLOADS" ]; then
  mkdir -p "$EXTRACT_UPLOADS" && tar -C "$EXTRACT_UPLOADS" -xf "$ART/uploads.tar"
  log "uploads extracted to $EXTRACT_UPLOADS ($(find "$EXTRACT_UPLOADS" -type f | wc -l) files)"
fi
if [ $RESTORE_UPLOADS -eq 1 ]; then
  service_running backend && die "backend was started during restore — refusing to replace uploads volume" 3
  docker_volume_exists "$UPLOADS_VOL" || docker volume create \
    --label "com.docker.compose.project=$(dc config --format json | jq -r .name)" --label "com.docker.compose.volume=uploads_data" "$UPLOADS_VOL" >/dev/null
  docker run --rm -v "$UPLOADS_VOL:/dst" -v "$ART:/src:ro" "$HELPER_IMAGE" \
    sh -c 'find /dst -mindepth 1 -delete && tar -C /dst -xf /src/uploads.tar' \
    || die "uploads volume restore FAILED" 8
  WANT="$(jq -r '.uploads.files // 0' "$ART/manifest.json")"
  GOT="$(docker run --rm -v "$UPLOADS_VOL:/dst:ro" "$HELPER_IMAGE" sh -c 'find /dst -type f | wc -l' | tr -d ' \r')"
  [ "$WANT" = "$GOT" ] || die "uploads verification FAILED: manifest files=$WANT, volume files=$GOT" 8
  log "uploads restored into volume $UPLOADS_VOL ($GOT files)"
elif [ -f "$ART/uploads.tar" ] && [ -z "$EXTRACT_UPLOADS" ]; then
  log "uploads.tar present ($(jq -r '.uploads.files // "?"' "$ART/manifest.json") files) — not applied; use --restore-uploads --yes (backend stopped) or --extract-uploads DIR"
fi

# ── 6. Redis ──
if [ $WITH_REDIS -eq 1 ]; then
  log "redis: replacing volume $REDIS_VOL from redis/dump.rdb (dbsize at capture: $WANT_DBSIZE, captured $(jq -r .redis.rdb_captured_at "$ART/manifest.json"))"
  service_running "$REDIS_SERVICE" && dc stop "$REDIS_SERVICE"
  docker_volume_exists "$REDIS_VOL" || docker volume create \
    --label "com.docker.compose.project=$(dc config --format json | jq -r .name)" --label "com.docker.compose.volume=redis_data" "$REDIS_VOL" >/dev/null
  docker run --rm -v "$REDIS_VOL:/data" -v "$ART/redis:/src:ro" "$HELPER_IMAGE" \
    sh -c 'find /data -mindepth 1 -delete && cp /src/dump.rdb /data/dump.rdb' || die "could not stage dump.rdb into $REDIS_VOL" 7
  TMPREDIS="$(docker run -d --rm -v "$REDIS_VOL:/data" "$REDIS_IMAGE" redis-server --appendonly no --save "" --dir /data)"
  rcli() { docker exec "$TMPREDIS" redis-cli "$@" | tr -d '\r'; }
  rinfo() { rcli INFO persistence | awk -F: -v k="$1" '$1 == k { print $2 }'; }
  redis_fail() { docker kill "$TMPREDIS" >/dev/null 2>&1 || true; die "$1" 7; }
  for _ in $(seq 1 60); do [ "$(rcli PING 2>/dev/null)" = "PONG" ] && break; sleep 1; done
  [ "$(rcli PING 2>/dev/null)" = "PONG" ] || redis_fail "temporary redis did not start"
  [ "$(rinfo loading)" = "0" ] || { sleep 2; [ "$(rinfo loading)" = "0" ] || redis_fail "redis still loading dump.rdb"; }
  GOT_DBSIZE="$(rcli DBSIZE)"
  [ "$GOT_DBSIZE" = "$WANT_DBSIZE" ] || redis_fail "redis restore FAILED: dump.rdb loaded dbsize=$GOT_DBSIZE, manifest dbsize=$WANT_DBSIZE"
  [ "$(rcli CONFIG SET appendonly yes)" = "OK" ] || redis_fail "CONFIG SET appendonly yes failed"
  for _ in $(seq 1 180); do
    if [ "$(rinfo aof_enabled)" = "1" ] && [ "$(rinfo aof_rewrite_in_progress)" = "0" ] && [ "$(rinfo aof_rewrite_scheduled)" = "0" ]; then break; fi
    sleep 1
  done
  [ "$(rinfo aof_last_bgrewrite_status)" = "ok" ] || redis_fail "AOF rewrite from restored dataset did not finish ok"
  rcli SHUTDOWN >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do docker inspect "$TMPREDIS" >/dev/null 2>&1 || break; sleep 1; done
  dc start "$REDIS_SERVICE" >/dev/null 2>&1 || dc up -d --no-deps "$REDIS_SERVICE"
  wait_service_healthy "$REDIS_SERVICE" 120 || die "redis service did not become healthy after restore" 7
  FINAL_DBSIZE="$(dc exec -T "$REDIS_SERVICE" redis-cli DBSIZE | tr -d '\r')"
  [ "$FINAL_DBSIZE" = "$WANT_DBSIZE" ] || die "redis restore FAILED: after restart dbsize=$FINAL_DBSIZE, manifest dbsize=$WANT_DBSIZE (AOF from RDB rewrite not picked up?)" 7
  log "redis restored: dbsize=$FINAL_DBSIZE, AOF rebuilt from dump.rdb. Reservations/locks are as of capture time — run /cron/reconcile-reserves after services start."
else
  log "redis NOT restored (by design). Source of truth is Postgres: after switching DATABASE_URL run scripts/redis_rebuild_spend.py --apply and /cron/reconcile-reserves."
fi

log "restore completed AND verification passed: database=$TARGET"
[ "$TARGET" = "$SRC_DB" ] || log "next: stop backend/scheduler/bidder-go, point DATABASE_URL at $TARGET (or ALTER DATABASE ... RENAME), start services, check GET /health"
exit 0
