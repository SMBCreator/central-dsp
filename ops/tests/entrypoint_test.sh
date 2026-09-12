#!/usr/bin/env bash
# Контракт backend/docker-entrypoint.sh без Docker и без root: подставные `id`
# (=root), `chown` и `setpriv` в PATH пишут вызовы в журнал. Реальную смену
# владельца volume проверяет CI job container-smoke (ops/tests/uploads_owner_docker.sh).
# Проверяем:
#   * владелец уже верный -> ни одного chown, exec через setpriv 10001:10001;
#   * legacy root-owned каталог -> chown каталога + только чужих файлов поддерева,
#     без -R и без выхода за каталог; symlink внутри не раскрывается;
#   * symlink вместо каталога -> отказ (exit 78), chown не вызывался;
#   * UPLOAD_DIR вне allowlist (/, /usr, /app, /etc, /data) -> отказ, chown не вызывался;
#   * каталог отсутствует -> создаётся и отдаётся app;
#   * уже не root -> exec без setpriv/chown;
#   * setpriv получает --no-new-privs/--clear-groups/-all caps и исходную команду.
#   ops/tests/entrypoint_test.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EP="$ROOT/backend/docker-entrypoint.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
BIN="$T/bin"; mkdir -p "$BIN"
export CALLS="$T/calls.log"

cat >"$BIN/id" <<'FAKE'
#!/usr/bin/env bash
echo "${FAKE_UID:-0}"
FAKE
cat >"$BIN/chown" <<'FAKE'
#!/usr/bin/env bash
echo "chown $*" >>"$CALLS"
FAKE
cat >"$BIN/setpriv" <<'FAKE'
#!/usr/bin/env bash
echo "setpriv $*" >>"$CALLS"
while [ $# -gt 0 ]; do [ "$1" = "--" ] && { shift; break; }; shift; done
exec "$@"
FAKE
chmod +x "$BIN"/*
export PATH="$BIN:$PATH"

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   $1"; }
bad() { fail=$((fail+1)); echo "  FAIL $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/       /'; }
reset() { : >"$CALLS"; }
run_ep() { # run_ep <UPLOAD_DIR> [VAR=val ...]; echo rc
  local dir="$1"; shift
  env UPLOAD_DIR="$dir" ENTRYPOINT_ALLOW_PATH="$dir" "$@" "$EP" sh -c 'echo APP_RAN:$*' -- app-cmd --flag >"$T/stdout" 2>"$T/stderr"
  echo $?
}
me_uid="$(/usr/bin/id -u)"; me_gid="$(/usr/bin/id -g)"  # настоящий id, не фейк из PATH

# ---------------------------------------------------------------- 1. владелец верный
reset; d="$T/ok/uploads"; mkdir -p "$d/sub"; touch "$d/a.png" "$d/sub/b.mp4"
rc="$(run_ep "$d" APP_UID="$me_uid" APP_GID="$me_gid")"
if [ "$rc" = 0 ] && ! grep -q '^chown' "$CALLS" && grep -q 'APP_RAN:app-cmd --flag' "$T/stdout" \
   && grep -q "already owned by $me_uid:$me_gid" "$T/stderr"; then ok "correct owner: no chown, app exec'd"; else bad "correct owner" "$(cat "$CALLS" "$T/stderr")"; fi

# ---------------------------------------------------------------- 2. legacy root-owned
reset; d="$T/legacy/uploads"; mkdir -p "$d/sub"; touch "$d/a.png" "$d/sub/b.mp4"
ln -s /etc/passwd "$d/evil-link"; mkdir -p "$T/outside"; touch "$T/outside/c.txt"; ln -s "$T/outside" "$d/dir-link"
rc="$(run_ep "$d" APP_UID=10001 APP_GID=10001)"
n_dir="$(grep -c "^chown -h 10001:10001 $d\$" "$CALLS")"
n_tree="$(grep -c "^chown -h 10001:10001 " "$CALLS")"
if [ "$rc" = 0 ] && [ "$n_dir" = 1 ] && grep -q "legacy root-owned volume" "$T/stderr" \
   && grep -q "fixed owner on 5 entries" "$T/stderr" \
   && ! grep -q -- " -R\| $T/outside/c.txt\|/etc/passwd" "$CALLS" \
   && grep -q "APP_RAN" "$T/stdout"; then ok "legacy owner: dir chown + 5 subtree entries (incl. symlinks themselves), no -R, no follow"; else bad "legacy owner (n_dir=$n_dir n_tree=$n_tree rc=$rc)" "$(cat "$CALLS" "$T/stderr")"; fi

# ---------------------------------------------------------------- 3. scan off
reset; rc="$(run_ep "$d" APP_UID=10001 APP_GID=10001 UPLOADS_OWNERSHIP_SCAN=0)"
if [ "$rc" = 0 ] && [ "$(grep -c '^chown' "$CALLS")" = 1 ] && grep -q "subtree scan skipped" "$T/stderr"; then ok "UPLOADS_OWNERSHIP_SCAN=0: only dir chown"; else bad "scan off" "$(cat "$CALLS" "$T/stderr")"; fi

# ---------------------------------------------------------------- 4. symlink вместо каталога
reset; mkdir -p "$T/target"; ln -s "$T/target" "$T/linkdir"
rc="$(run_ep "$T/linkdir" APP_UID=10001 APP_GID=10001)"
if [ "$rc" = 78 ] && ! grep -q '^chown' "$CALLS" && grep -q "is a symlink; refusing" "$T/stderr" && ! grep -q APP_RAN "$T/stdout"; then ok "symlink upload dir refused, app not started"; else bad "symlink refused (rc=$rc)" "$(cat "$CALLS" "$T/stderr")"; fi

# ---------------------------------------------------------------- 5. allowlist
for bad_path in / /usr /etc /app /data /var/run "$T/legacy/uploads"; do
  reset
  rc="$(env UPLOAD_DIR="$bad_path" APP_UID=10001 APP_GID=10001 "$EP" sh -c 'echo APP_RAN' >"$T/stdout" 2>"$T/stderr"; echo $?)"
  if [ "$rc" = 78 ] && ! grep -q '^chown' "$CALLS" && grep -q "outside the allowed locations" "$T/stderr" && ! grep -q APP_RAN "$T/stdout"; then ok "allowlist rejects UPLOAD_DIR=$bad_path"; else bad "allowlist $bad_path (rc=$rc)" "$(cat "$CALLS" "$T/stderr")"; fi
done
# и наоборот: /data/uploads проходит allowlist (реального каталога нет -> mkdir-ветка недоступна без root; проверяем только, что отказа по allowlist нет)
reset; rc="$(env UPLOAD_DIR=/data/uploads APP_UID=10001 APP_GID=10001 "$EP" true >"$T/stdout" 2>"$T/stderr"; echo $?)"
if ! grep -q "outside the allowed locations" "$T/stderr"; then ok "allowlist accepts /data/uploads"; else bad "allowlist /data/uploads" "$(cat "$T/stderr")"; fi

# ---------------------------------------------------------------- 6. каталог отсутствует
reset; d="$T/fresh/uploads"
rc="$(run_ep "$d" APP_UID=10001 APP_GID=10001)"
if [ "$rc" = 0 ] && [ -d "$d" ] && grep -q "^chown 10001:10001 $d\$" "$CALLS" && grep -q "did not exist: created" "$T/stderr"; then ok "missing dir created and handed to app"; else bad "missing dir (rc=$rc)" "$(cat "$CALLS" "$T/stderr")"; fi

# ---------------------------------------------------------------- 7. уже не root
reset; d="$T/ok/uploads"
rc="$(run_ep "$d" FAKE_UID=10001 APP_UID=10001 APP_GID=10001)"
if [ "$rc" = 0 ] && [ ! -s "$CALLS" ] && grep -q "APP_RAN:app-cmd --flag" "$T/stdout" && grep -q "already running as uid 10001" "$T/stderr"; then ok "non-root start: plain exec, no setpriv/chown"; else bad "non-root start" "$(cat "$CALLS" "$T/stderr")"; fi

# ---------------------------------------------------------------- 8. setpriv контракт
reset; d="$T/ok/uploads"; run_ep "$d" APP_UID=10001 APP_GID=10001 >/dev/null
sp="$(grep '^setpriv' "$CALLS")"
want="setpriv --reuid=10001 --regid=10001 --clear-groups --inh-caps=-all --bounding-set=-all --no-new-privs -- sh -c echo APP_RAN:\$* -- app-cmd --flag"
if [ "$sp" = "$want" ]; then ok "setpriv drops uid/gid/groups/caps, no_new_privs, passes command"; else bad "setpriv args" "got:  $sp
want: $want"; fi

echo "entrypoint_test: $pass passed, $fail failed"
[ "$fail" = 0 ]
