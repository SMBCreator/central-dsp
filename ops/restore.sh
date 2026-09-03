#!/usr/bin/env bash
# Восстановление CentralDSP из бэкапа, снятого ops/backup.sh.
#
#   ops/restore.sh <backup-dir|backup.tar.gz> [--target-db NAME] [--yes]
#
# По умолчанию восстанавливает в НОВУЮ базу centraldsp_restore_<ts> — чтобы
# сначала посмотреть данные, потом переключить приложение (DATABASE_URL) или
# переименовать базы. В боевую БД напрямую — только с --target-db и --yes.
set -euo pipefail

SRC="${1:?Укажите каталог бэкапа или .tar.gz}"; shift || true
TARGET_DB=""
YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --target-db) TARGET_DB="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

PG_CONTAINER="${PG_CONTAINER:-centraldsp-postgres}"
PG_USER="${PG_USER:-centraldsp}"
UPLOADS_DIR="${UPLOADS_DIR:-/var/lib/centraldsp/uploads}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

WORK="$SRC"
if [[ "$SRC" == *.tar.gz ]]; then
  WORK="$(mktemp -d)"
  tar -xzf "$SRC" -C "$WORK" --strip-components=1
fi
DUMP="$(ls "$WORK"/postgres-*.dump | head -1)"
[ -f "$DUMP" ] || { echo "postgres-*.dump не найден в $WORK" >&2; exit 1; }

if [ -f "$WORK/manifest.json" ]; then
  echo "manifest:"; cat "$WORK/manifest.json"; echo
fi
echo "verify dump: $(sha256sum "$DUMP" | cut -d' ' -f1)"

TARGET="${TARGET_DB:-centraldsp_restore_${STAMP}}"
if [ -n "$TARGET_DB" ] && [ "$YES" -ne 1 ]; then
  echo "Восстановление В СУЩЕСТВУЮЩУЮ базу $TARGET_DB перезапишет данные. Повторите с --yes." >&2
  exit 3
fi

echo "== Postgres -> $TARGET"
if [ -z "$TARGET_DB" ]; then
  docker exec "$PG_CONTAINER" createdb -U "$PG_USER" "$TARGET"
fi
docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$TARGET" \
  --clean --if-exists --no-owner --no-privileges --jobs=4 < "$DUMP"

echo "== Проверка"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TARGET" -Atc \
  "SELECT 'campaigns='||count(*) FROM campaigns
   UNION ALL SELECT 'creatives='||count(*) FROM creatives
   UNION ALL SELECT 'bid_events='||count(*) FROM bid_events
   UNION ALL SELECT 'last_migration='||coalesce(max(version)::text,'?') FROM schema_migrations"

if [ -f "$WORK/uploads.tar.gz" ]; then
  echo "== uploads -> ${UPLOADS_DIR}.restore_${STAMP}"
  mkdir -p "${UPLOADS_DIR}.restore_${STAMP}"
  tar -xzf "$WORK/uploads.tar.gz" -C "${UPLOADS_DIR}.restore_${STAMP}"
  echo "Сверьте и замените: mv ${UPLOADS_DIR} ${UPLOADS_DIR}.old && mv ${UPLOADS_DIR}.restore_${STAMP} ${UPLOADS_DIR}"
fi

cat <<EOF

Готово. База: $TARGET
Переключение приложения:
  1) docker compose stop backend bidder
  2) DATABASE_URL -> .../$TARGET  (или ALTER DATABASE ... RENAME)
  3) docker compose up -d backend bidder
  4) redis-cli FLUSHDB  -- счётчики бюджетов пересчитаются из БД
EOF
