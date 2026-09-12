#!/bin/sh
# Backend/scheduler entrypoint (audit O-07 / C6): стартует от root ТОЛЬКО чтобы
# починить владельца volume с креативами, затем сбрасывает привилегии и
# запускает процесс от app (10001:10001). Python никогда не работает как root.
#
# Зачем: uploads_data на VPS мог быть создан прежней root-версией образа.
# Простой USER 10001 в Dockerfile упал бы на первом же upload с EACCES.
# Ручной chown на хосте — только emergency fallback (docs/DEPLOYMENT.md §8.1),
# штатный апгрейд должен переживать legacy volume сам.
#
# Гарантии:
#   * трогаем только каталог UPLOAD_DIR (по умолчанию /data/uploads) и его поддерево;
#   * никакого chown -R / или системных каталогов — путь проверяется allowlist'ом;
#   * symlink на месте каталога -> отказ (защита от подмены цели chown);
#   * не пересекаем границы mount'ов (-xdev), не следуем symlink (-h / find -P);
#   * если владелец уже верный — ни одного chown; иначе один проход find по поддереву
#     (UPLOADS_OWNERSHIP_SCAN=0 отключает проход, если volume огромный и уже проверен);
#   * exec setpriv: uid/gid 10001, без supplementary groups, capabilities и
#     no_new_privs — процесс не сможет вернуть root.
set -eu

APP_UID="${APP_UID:-10001}"
APP_GID="${APP_GID:-10001}"
UPLOADS="${UPLOAD_DIR:-/data/uploads}"
SCAN="${UPLOADS_OWNERSHIP_SCAN:-1}"

log() { printf 'entrypoint: %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 78; }  # EX_CONFIG

allowed_path() {
  # Только каталоги под /data/<name>... и /app/uploads (dev). Всё остальное — отказ.
  case "$1" in
    /data/?*) return 0 ;;
    /app/uploads|/app/uploads/*) return 0 ;;
  esac
  # Тестовый hook (ops/tests/entrypoint_test.sh): один точный путь. Compose его
  # не задаёт; в production появление этой строки в логе — повод разобраться.
  if [ -n "${ENTRYPOINT_ALLOW_PATH:-}" ] && [ "$1" = "$ENTRYPOINT_ALLOW_PATH" ]; then
    log "WARNING: UPLOAD_DIR allowed via ENTRYPOINT_ALLOW_PATH (test hook)"
    return 0
  fi
  return 1
}

fix_uploads_owner() {
  if [ ! -e "$UPLOADS" ]; then
    # Нет mount'а (например, scheduler или CI smoke без volume) — создаём в
    # файловой системе контейнера, чтобы приложение не падало на makedirs.
    mkdir -p "$UPLOADS"
    chown "$APP_UID:$APP_GID" "$UPLOADS"
    log "uploads dir $UPLOADS did not exist: created, owner $APP_UID:$APP_GID"
    return
  fi
  [ -L "$UPLOADS" ] && die "uploads path $UPLOADS is a symlink; refusing to chown"
  [ -d "$UPLOADS" ] || die "uploads path $UPLOADS is not a directory"

  owner="$(stat -c '%u:%g' "$UPLOADS")"
  if [ "$owner" = "$APP_UID:$APP_GID" ]; then
    log "uploads dir $UPLOADS already owned by $owner"
  else
    log "uploads dir $UPLOADS owned by $owner (legacy root-owned volume?) -> chown $APP_UID:$APP_GID"
    chown -h "$APP_UID:$APP_GID" "$UPLOADS"
  fi
  # Владелец должен иметь rwx на каталог, иначе новые файлы не создаются.
  chmod u+rwx "$UPLOADS"

  if [ "$SCAN" = "1" ]; then
    # Один проход по поддереву: чужие файлы (root после ручного копирования,
    # старые загрузки) -> app. Никаких symlink-переходов, не выходим за mount.
    fixed="$(find "$UPLOADS" -xdev -mindepth 1 \( ! -user "$APP_UID" -o ! -group "$APP_GID" \) \
      -exec chown -h "$APP_UID:$APP_GID" {} + -print | wc -l)"
    if [ "$fixed" -gt 0 ]; then
      log "fixed owner on $fixed entries under $UPLOADS"
    fi
  else
    log "UPLOADS_OWNERSHIP_SCAN=0: subtree scan skipped"
  fi
}

if [ "$(id -u)" != "0" ]; then
  log "already running as uid $(id -u); no ownership fix, exec directly"
  exec "$@"
fi

allowed_path "$UPLOADS" || die "UPLOAD_DIR=$UPLOADS is outside the allowed locations (/data/<dir>, /app/uploads)"
fix_uploads_owner

log "dropping privileges -> uid=$APP_UID gid=$APP_GID, exec: $*"
exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups \
  --inh-caps=-all --bounding-set=-all --no-new-privs -- "$@"
