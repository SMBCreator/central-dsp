#!/usr/bin/env bash
# Disaster-recovery drill: backup -> уничтожение -> restore -> финансовая сверка (C2 / O-02).
#
# Запускается отдельным CI job (.github/workflows/ci.yml: backup-restore-drill) и вручную:
#
#   CENTRALDSP_TEST_MODE=1 COMPOSE_PROJECT_NAME=centraldsp-ci-local ops/tests/backup_restore_drill.sh
#
# Сценарий (все шаги обязательны, любой сбой = non-zero):
#   1. поднять ИЗОЛИРОВАННЫЙ compose-проект (postgres + redis, свои volumes, свои порты);
#   2. накатить миграции (backend/migrate.py);
#   3. seed: advertiser x3, campaign, line_item, 153 проводок, 500 bid_events, ключи Redis,
#      файлы в uploads volume (ops/tests/seed_drill.py);
#   4. ops/backup.sh --with-redis, VERIFY=always (внутри — restore --scratch + сверка);
#   5. проверить manifest.json / SHA256SUMS / состав артефакта;
#   6. negative: испорченный артефакт -> restore exit 5, база не создаётся;
#      --target-db без --yes -> exit 3;
#   7. `docker compose down -v` ТОЛЬКО этого проекта: база, redis и uploads уничтожены;
#   8. поднять пустой стек, ops/restore.sh --target-db <db> --yes --restore-uploads
#      --with-redis --yes-replace-redis;
#   9. независимая сверка seed_drill.py check (не через код restore.sh) + Redis + uploads;
#  10. negative: подменить balance -> restore.sh --verify-only -> exit 4;
#  11. снести test containers и volumes (trap, всегда).
#
# SAFETY GUARD — до любого destructive действия:
#   * CENTRALDSP_TEST_MODE=1;
#   * COMPOSE_PROJECT_NAME начинается с centraldsp-ci-;
#   * DATABASE_URL / REDIS_URL либо не заданы, либо указывают на 127.0.0.1/localhost;
#   * все volumes проекта имеют префикс <COMPOSE_PROJECT_NAME>_ (compose это гарантирует,
#     но проверяем по `docker compose config`), production .env не читается
#     (COMPOSE_ENV_FILES=ops/tests/drill.env).
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
# shellcheck source=ops/lib/backup_common.sh
. "$REPO/ops/lib/backup_common.sh"

# ── guard ──
[ "${CENTRALDSP_TEST_MODE:-}" = "1" ] || die "guard: CENTRALDSP_TEST_MODE=1 is required (this drill destroys a database)" 3
[[ "${COMPOSE_PROJECT_NAME:-}" =~ ^centraldsp-ci-[a-z0-9_-]+$ ]] || die "guard: COMPOSE_PROJECT_NAME must start with centraldsp-ci- (got '${COMPOSE_PROJECT_NAME:-}')" 3
for v in DATABASE_URL REDIS_URL; do
  val="${!v:-}"
  [ -z "$val" ] || [[ "$val" =~ @(127\.0\.0\.1|localhost)[:/] ]] || [[ "$val" =~ ://(127\.0\.0\.1|localhost)[:/] ]] \
    || die "guard: $v points at a non-local host — refusing to run a destructive drill in this environment" 3
done
need_cmd docker jq tar sha256sum awk diff python3 curl

export COMPOSE_PROJECT_NAME
export COMPOSE_FILE="$REPO/compose.yml:$HERE/compose.ci-drill.yml"
export COMPOSE_ENV_FILES="$HERE/drill.env"
set -a; . "$HERE/drill.env"; set +a          # POSTGRES_*, DRILL_* для скриптов и python
export COMPOSE_DIR="$REPO"
PG_USER="$POSTGRES_USER"
unset DATABASE_URL REDIS_URL

DSN="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:$DRILL_PG_PORT/$POSTGRES_DB"
WORK="$(mktemp -d "/tmp/cdsp-drill-XXXXXX")"
REPORT="$WORK/report"; mkdir -p "$REPORT" "$WORK/backups"
export BACKUP_DIR="$WORK/backups" RESTORE_TMP="$WORK"

step() { printf '\n===== %s =====\n' "$*"; }
cleanup() {
  local rc=$?
  step "cleanup (rc=$rc)"
  dc down -v --remove-orphans --timeout 10 >/dev/null 2>&1 || true
  cp -f "$BACKUP_DIR"/*.tar "$REPORT/" 2>/dev/null || true
  if [ $rc -eq 0 ]; then log "DRILL PASSED — report: $REPORT"; else log "DRILL FAILED rc=$rc — report: $REPORT"; fi
}
trap cleanup EXIT

# guard, часть 2: конфиг compose реально изолирован
CFG="$(dc config --format json)"
[ "$(jq -r .name <<<"$CFG")" = "$COMPOSE_PROJECT_NAME" ] || die "guard: compose project name mismatch" 3
while read -r vol; do
  [[ "$vol" == "${COMPOSE_PROJECT_NAME}_"* ]] || die "guard: volume '$vol' is not namespaced by the test project" 3
done < <(jq -r '.volumes[].name' <<<"$CFG")
case "$POSTGRES_DB" in *_ci) ;; *) die "guard: drill database must end with _ci (got $POSTGRES_DB)" 3 ;; esac
while read -r net; do
  [[ "$net" == "${COMPOSE_PROJECT_NAME}-"* ]] || die "guard: network '$net' is shared with a non-test project" 3
done < <(jq -r '.networks[].name' <<<"$CFG")
log "guard ok: project=$COMPOSE_PROJECT_NAME db=$POSTGRES_DB volumes=$(jq -r '[.volumes[].name] | join(",")' <<<"$CFG")"

# ── 1. стек ──
step "1. up isolated stack"
dc down -v --remove-orphans >/dev/null 2>&1 || true
dc up -d --no-deps postgres redis
wait_service_healthy postgres 120 || die "postgres not healthy" 1
wait_service_healthy redis 60 || die "redis not healthy" 1

# ── 2. migrations ──
step "2. migrations"
python3 -c "import asyncpg" 2>/dev/null || pip install -q asyncpg
( cd "$REPO/backend" && DATABASE_URL="$DSN" python3 migrate.py ) | tail -n 3
MIG_N="$(pg_sql "$POSTGRES_DB" 'SELECT count(*) FROM schema_migrations')"
[ "${MIG_N:-0}" -gt 0 ] || die "no migrations applied" 1
log "migrations applied: $MIG_N"

# ── 3. seed ──
step "3. seed"
python3 "$HERE/seed_drill.py" seed --dsn "$DSN" --out "$REPORT/expected.json"
dc exec -T redis redis-cli SET "spend:camp:drill:$(date -u +%F)" 4211 >/dev/null
dc exec -T redis redis-cli SET "res:adv:drill" 10.5 >/dev/null
dc exec -T redis redis-cli HSET "drill:hash" a 1 b 2 >/dev/null
REDIS_KEYS="$(dc exec -T redis redis-cli DBSIZE | tr -d '\r')"
UPLOADS_VOL="$(compose_volume_name uploads_data)"
docker volume create --label "com.docker.compose.project=$COMPOSE_PROJECT_NAME" --label "com.docker.compose.volume=uploads_data" "$UPLOADS_VOL" >/dev/null
docker run --rm -v "$UPLOADS_VOL:/u" "${HELPER_IMAGE:-alpine:3.20}" sh -c 'mkdir -p /u/creatives && printf "drill-banner" > /u/creatives/banner.txt && head -c 65536 /dev/urandom > /u/creatives/video.bin && ls -la /u/creatives' 
UPLOADS_SHA="$(docker run --rm -v "$UPLOADS_VOL:/u:ro" "${HELPER_IMAGE:-alpine:3.20}" sh -c 'cd /u && find . -type f | sort | xargs sha256sum')"
log "seeded: redis keys=$REDIS_KEYS uploads files=$(echo "$UPLOADS_SHA" | wc -l)"

# ── 4. backup (с VERIFY=always -> restore --scratch внутри) ──
step "4. backup.sh --with-redis (VERIFY=always)"
VERIFY=always "$REPO/ops/backup.sh" --with-redis 2>&1 | tee "$REPORT/backup.log"
TAR="$(ls -1 "$BACKUP_DIR"/backup-*.tar | head -n1)"
[ -f "$TAR" ] || die "no artifact produced" 1

# ── 5. artifact contents ──
step "5. manifest / checksums"
mkdir -p "$WORK/inspect" && tar -C "$WORK/inspect" -xf "$TAR"
ART="$(find "$WORK/inspect" -mindepth 1 -maxdepth 1 -type d | head -n1)"
cp "$ART/manifest.json" "$REPORT/manifest.json"
( cd "$ART" && sha256sum -c --quiet SHA256SUMS ) || die "SHA256SUMS does not verify on a fresh artifact" 1
for f in postgres.dump ledger_snapshot.tsv counts.tsv schema_migrations.tsv pg_restore_toc.txt uploads.tar redis/dump.rdb manifest.json SHA256SUMS; do
  [ -f "$ART/$f" ] || die "artifact missing $f" 1
done
M="$ART/manifest.json"
[ "$(jq -r .format_version "$M")" = "$BACKUP_FORMAT_VERSION" ] || die "manifest format_version" 1
[ "$(jq -r .counts.advertisers "$M")" = "3" ] || die "manifest advertisers != 3" 1
[ "$(jq -r .counts.transactions "$M")" = "$(jq -r .transactions "$REPORT/expected.json")" ] || die "manifest transactions != seed" 1
[ "$(jq -r .counts.transactions_total "$M")" = "$(jq -r .transactions_total "$REPORT/expected.json")" ] || die "manifest transactions_total != seed" 1
[ "$(jq -r .counts.bid_events "$M")" = "500" ] || die "manifest bid_events != 500" 1
[ "$(jq -r .schema.migrations_applied "$M")" = "$MIG_N" ] || die "manifest migrations != $MIG_N" 1
[ "$(jq -r .reconciliation.status "$M")" = "mismatch" ] || die "expected reconciliation.status=mismatch (advertiser C is seeded without ledger)" 1
[ "$(jq -r .reconciliation.advertisers_mismatch "$M")" = "1" ] || die "expected exactly 1 pre-existing mismatch" 1
[ "$(jq -r .reconciliation.advertisers_rounding "$M")" = "1" ] || die "expected exactly 1 rounding advertiser" 1
[ "$(jq -r .reconciliation.advertisers_ok "$M")" = "1" ] || die "expected exactly 1 ok advertiser" 1
[ "$(jq -r .redis.dbsize "$M")" = "$REDIS_KEYS" ] || die "manifest redis.dbsize != $REDIS_KEYS" 1
[ "$(jq -r .redis.aof_enabled "$M")" = "true" ] || die "manifest redis.aof_enabled should be true" 1
[ "$(jq -r .uploads.files "$M")" = "2" ] || die "manifest uploads.files != 2" 1
grep -q "VERIFY passed" "$REPORT/backup.log" || die "backup.sh VERIFY=always did not report 'VERIFY passed'" 1
log "manifest ok: $(jq -c '{counts, reconciliation: .reconciliation.status, schema: .schema.last_migration, redis: .redis.dbsize, uploads: .uploads.files}' "$M")"

# ── 6. negative: corrupted artifact, missing --yes ──
step "6. negative tests before destruction"
BAD="$WORK/bad.tar"; mkdir -p "$WORK/bad" && tar -C "$WORK/bad" -xf "$TAR"
BADART="$(find "$WORK/bad" -mindepth 1 -maxdepth 1 -type d | head -n1)"
printf 'corrupt' >> "$BADART/postgres.dump"
tar -C "$WORK/bad" -cf "$BAD" "$(basename "$BADART")"
DBS_BEFORE="$(pg_sql postgres "SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database")"
set +e; "$REPO/ops/restore.sh" "$BAD" > "$REPORT/negative_checksum.log" 2>&1; rc=$?; set -e
[ $rc -eq 5 ] || { cat "$REPORT/negative_checksum.log"; die "corrupted artifact: expected exit 5, got $rc" 1; }
[ "$(pg_sql postgres "SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database")" = "$DBS_BEFORE" ] || die "corrupted artifact created a database!" 1
set +e; "$REPO/ops/restore.sh" "$TAR" --target-db "$POSTGRES_DB" > "$REPORT/negative_noyes.log" 2>&1; rc=$?; set -e
[ $rc -eq 3 ] || { cat "$REPORT/negative_noyes.log"; die "--target-db without --yes: expected exit 3, got $rc" 1; }
set +e; "$REPO/ops/restore.sh" "$TAR" --with-redis > "$REPORT/negative_redis.log" 2>&1; rc=$?; set -e
[ $rc -eq 3 ] || die "--with-redis without --yes-replace-redis: expected exit 3, got $rc" 1
log "negative tests ok: corrupted->5 (no db created), no --yes->3, redis without confirm->3"

# ── 7. destroy ──
step "7. destroy test stack (down -v)"
dc down -v --timeout 10
docker_volume_exists "$UPLOADS_VOL" && die "uploads volume survived down -v" 1
docker_volume_exists "$(compose_volume_name postgres_data)" && die "postgres volume survived down -v" 1
log "destroyed: postgres/redis/uploads volumes of $COMPOSE_PROJECT_NAME"

# ── 8. restore ──
step "8. restore into fresh stack"
dc up -d --no-deps postgres redis
wait_service_healthy postgres 120 || die "postgres not healthy" 1
wait_service_healthy redis 60 || die "redis not healthy" 1
[ "$(dc exec -T redis redis-cli DBSIZE | tr -d '\r')" = "0" ] || die "fresh redis is not empty" 1
[ "$(pg_sql "$POSTGRES_DB" "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")" = "0" ] || die "fresh db is not empty" 1
"$REPO/ops/restore.sh" "$TAR" --target-db "$POSTGRES_DB" --yes --restore-uploads --with-redis --yes-replace-redis 2>&1 | tee "$REPORT/restore.log"
grep -q "restore completed AND verification passed" "$REPORT/restore.log" || die "restore.sh did not report 'verification passed'" 1

# ── 9. независимая сверка ──
step "9. independent reconciliation (seed_drill.py check, redis, uploads)"
python3 "$HERE/seed_drill.py" check --dsn "$DSN" --expected "$REPORT/expected.json"
[ "$(dc exec -T redis redis-cli DBSIZE | tr -d '\r')" = "$REDIS_KEYS" ] || die "redis dbsize after restore != $REDIS_KEYS" 1
[ "$(dc exec -T redis redis-cli GET "spend:camp:drill:$(date -u +%F)" | tr -d '\r')" = "4211" ] || die "redis key content lost" 1
[ "$(dc exec -T redis redis-cli CONFIG GET appendonly | tr -d '\r' | tail -n1)" = "yes" ] || die "redis restarted without AOF" 1
[ "$(dc exec -T redis sh -c 'ls /data/appendonlydir | wc -l' | tr -d '\r ')" -ge 2 ] || die "AOF was not rebuilt from restored dataset" 1
AFTER_SHA="$(docker run --rm -v "$UPLOADS_VOL:/u:ro" "${HELPER_IMAGE:-alpine:3.20}" sh -c 'cd /u && find . -type f | sort | xargs sha256sum')"
[ "$AFTER_SHA" = "$UPLOADS_SHA" ] || { echo "$UPLOADS_SHA"; echo "$AFTER_SHA"; die "uploads content differs after restore" 1; }
# сверка после restore против manifest тем же путём, что и prod
"$REPO/ops/restore.sh" "$TAR" --verify-only --target-db "$POSTGRES_DB" > "$REPORT/verify_only.log" 2>&1 || die "--verify-only on restored db failed" 1
log "independent checks ok"

# ── 10. negative: подмена баланса должна валить verification ──
step "10. negative: tampered balance -> verification FAILED (exit 4)"
python3 "$HERE/seed_drill.py" tamper --dsn "$DSN"
set +e; "$REPO/ops/restore.sh" "$TAR" --verify-only --target-db "$POSTGRES_DB" > "$REPORT/negative_tamper.log" 2>&1; rc=$?; set -e
[ $rc -eq 4 ] || { cat "$REPORT/negative_tamper.log"; die "tampered balance: expected exit 4, got $rc" 1; }
grep -q "verification FAILED" "$REPORT/negative_tamper.log" || die "no 'verification FAILED' in output" 1
grep -q "expected: balance=" "$REPORT/negative_tamper.log" || die "no expected/actual balance report in output" 1
# и что restore.sh ничего не «починил» сам: баланс остался подменённым, проводок не добавилось
[ "$(pg_sql "$POSTGRES_DB" "SELECT count(*) FROM transactions")" = "$(jq -r .transactions "$REPORT/expected.json")" ] || die "restore.sh created transactions!" 1
log "tamper detected, nothing auto-corrected"

step "DRILL COMPLETE"
exit 0
