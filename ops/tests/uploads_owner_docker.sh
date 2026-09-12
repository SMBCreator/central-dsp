#!/usr/bin/env bash
# Container smoke backend-образа с РЕАЛЬНЫМ Docker (CI job container-smoke,
# audit O-07 / C6). Проверяет, что апгрейд с root-версии образа переживает
# существующий root-owned volume uploads_data и что процесс не root:
#   [x] новый volume: процесс от 10001 может создать файл;
#   [x] legacy root-owned volume (каталог + файлы): entrypoint чинит владельца;
#   [x] существующий креатив читается, удаляется, новый пишется;
#   [x] повторный старт: «already owned», без chown (идемпотентно, без -R на каждом рестарте);
#   [x] процесс: uid=gid=10001, CapEff=0, NoNewPrivs=1;
#   [x] запись вне uploads (/etc, /app, /usr, /data) невозможна;
#   [x] symlink внутри uploads не раскрывается (цель вне volume не тронута);
#   [x] entrypoint не запускает команду от root (id -u внутри = 10001).
# Exit: 0 PASS, 1 FAIL, 2 usage.
#   BACKEND_IMAGE=centraldsp/backend:local ops/tests/uploads_owner_docker.sh
set -uo pipefail
IMG="${BACKEND_IMAGE:-centraldsp/backend:local}"
command -v docker >/dev/null || { echo "docker not found" >&2; exit 2; }
docker image inspect "$IMG" >/dev/null 2>&1 || { echo "image $IMG not found (build it first)" >&2; exit 2; }
SUF="$(date +%s)-$$"
VOL_NEW="cdsp-ci-uploads-new-$SUF"; VOL_OLD="cdsp-ci-uploads-legacy-$SUF"
cleanup() { docker volume rm -f "$VOL_NEW" "$VOL_OLD" >/dev/null 2>&1 || true; }
trap cleanup EXIT

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   $1"; }
bad() { fail=$((fail+1)); echo "  FAIL $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/       /'; }
# Запуск через entrypoint образа с командой-пробой вместо uvicorn.
# shellcheck disable=SC2016  # $-выражения внутри одинарных кавычек раскрывает sh контейнера
run_app() { # run_app <volume> <sh script>
  docker run --rm -v "$1:/data/uploads" "$IMG" sh -c "$2" 2>&1
}
# Подготовка volume от root, минуя entrypoint (имитация старого образа).
as_root() { docker run --rm --entrypoint sh -v "$1:/data/uploads" "$IMG" -c "$2" 2>&1; }

# ---------------------------------------------------------------- 1. новый volume
docker volume create "$VOL_NEW" >/dev/null
out="$(run_app "$VOL_NEW" 'id -u; id -g; echo hello > /data/uploads/fresh.bin && cat /data/uploads/fresh.bin; stat -c %u:%g /data/uploads')"
if grep -qx 10001 <<<"$out" && grep -qx hello <<<"$out" && grep -q "^10001:10001$" <<<"$out"; then ok "fresh volume: app (10001) can write, dir owned 10001:10001"; else bad "fresh volume" "$out"; fi

# ---------------------------------------------------------------- 2. legacy root-owned
docker volume create "$VOL_OLD" >/dev/null
as_root "$VOL_OLD" 'mkdir -p /data/uploads/2025 && printf "\211PNG old" > /data/uploads/2025/old.png && printf x > /data/uploads/root.bin && ln -s /etc/hostname /data/uploads/link-out && chown -R 0:0 /data/uploads && chmod 755 /data/uploads' >/dev/null
out="$(run_app "$VOL_OLD" 'id -u; stat -c "%n %u:%g" /data/uploads /data/uploads/2025 /data/uploads/2025/old.png /data/uploads/root.bin; stat -c "%n %u:%g" /etc/hostname; cat /data/uploads/2025/old.png | head -c 8 | tail -c 3; echo; echo new > /data/uploads/new.bin && echo WRITE_OK; rm /data/uploads/root.bin && echo DELETE_OK; echo upd >> /data/uploads/2025/old.png && echo UPDATE_OK')"
checks=0
grep -q "^/data/uploads 10001:10001$" <<<"$out" && grep -q "^/data/uploads/2025 10001:10001$" <<<"$out" \
  && grep -q "^/data/uploads/2025/old.png 10001:10001$" <<<"$out" && checks=$((checks+1))
grep -q "^/etc/hostname 0:0$" <<<"$out" && checks=$((checks+1))           # symlink target untouched
grep -q "^old$" <<<"$out" && checks=$((checks+1))                          # existing creative readable
grep -q WRITE_OK <<<"$out" && grep -q DELETE_OK <<<"$out" && grep -q UPDATE_OK <<<"$out" && checks=$((checks+1))
grep -q "legacy root-owned volume" <<<"$out" && grep -q "fixed owner on" <<<"$out" && checks=$((checks+1))
if [ "$checks" = 5 ]; then ok "legacy volume: owner fixed (dir+subtree), read/write/delete/update OK, symlink target untouched"; else bad "legacy volume ($checks/5)" "$out"; fi

# ---------------------------------------------------------------- 3. идемпотентность
out="$(run_app "$VOL_OLD" 'true')"
if grep -q "already owned by 10001:10001" <<<"$out" && ! grep -q "fixed owner on" <<<"$out" && ! grep -q "legacy" <<<"$out"; then ok "second start: no chown at all"; else bad "idempotent restart" "$out"; fi

# ---------------------------------------------------------------- 4. privileges
out="$(run_app "$VOL_NEW" 'id -u; id -g; id -G; grep -E "^(CapEff|CapBnd|NoNewPrivs):" /proc/self/status')"
if grep -qx 10001 <<<"$out" && grep -q "^CapEff:.0000000000000000$" <<<"$out" && grep -q "^CapBnd:.0000000000000000$" <<<"$out" \
   && grep -q "^NoNewPrivs:.1$" <<<"$out" && [ "$(grep -c '^10001$' <<<"$out")" -ge 2 ]; then ok "process: uid/gid 10001, no supplementary groups, CapEff/CapBnd=0, NoNewPrivs=1"; else bad "privileges" "$out"; fi

# ---------------------------------------------------------------- 5. запись вне uploads
out="$(run_app "$VOL_NEW" 'for d in /etc /app /usr/local /data /; do touch "$d/cdsp-escape" 2>/dev/null && echo "WROTE $d"; done; echo ESCAPE_CHECK_DONE')"
if ! grep -q "^WROTE" <<<"$out" && grep -q ESCAPE_CHECK_DONE <<<"$out"; then ok "cannot write outside uploads (/etc /app /usr/local /data /)"; else bad "write escape" "$out"; fi

# ---------------------------------------------------------------- 6. entrypoint never runs command as root
out="$(docker run --rm "$IMG" sh -c 'id -u' 2>/dev/null)"
if [ "$out" = 10001 ]; then ok "default entrypoint execs command as 10001 even without volume"; else bad "root leak (id -u=$out)"; fi
# и healthcheck-команда из compose тоже не root
out="$(docker run --rm --entrypoint setpriv "$IMG" --reuid=10001 --regid=10001 --clear-groups -- id -u 2>&1)"
if [ "$out" = 10001 ]; then ok "healthcheck setpriv path yields uid 10001"; else bad "healthcheck setpriv" "$out"; fi

echo "uploads_owner_docker: $pass passed, $fail failed"
[ "$fail" = 0 ]
