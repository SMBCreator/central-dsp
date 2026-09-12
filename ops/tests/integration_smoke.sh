#!/usr/bin/env bash
# Integration smoke реального Compose-стека (CI job integration, audit O-07 / C6):
# postgres + redis + backend + bidder-go из СОБРАННЫХ образов, изолированный
# проект centraldsp-ci-*. Проверяет то, что unit-тесты и `compose config` не
# могут: миграции при старте, /health и /ready backend, процесс backend от
# uid 10001 (не root), upload креатива в volume после «апгрейда» (volume заранее
# сделан root-owned), чтение и удаление загруженного файла, bidder /livez,
# /healthz и базовый RTB-запрос (не 5xx), graceful stop без SIGKILL.
# Guard: COMPOSE_PROJECT_NAME должен начинаться с centraldsp-ci-, иначе exit 3
# до любых действий. Production .env не читается (COMPOSE_ENV_FILES=integration.env,
# APP_ENV=production с фиктивными секретами полной длины — те же startup-проверки, что на VPS).
# Exit: 0 PASS, 1 FAIL, 2 usage, 3 guard.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 2
: "${COMPOSE_PROJECT_NAME:?set COMPOSE_PROJECT_NAME=centraldsp-ci-<id>}"
case "$COMPOSE_PROJECT_NAME" in centraldsp-ci-*) ;; *) echo "guard: COMPOSE_PROJECT_NAME must start with centraldsp-ci-" >&2; exit 3 ;; esac
export COMPOSE_FILE="compose.yml:ops/tests/compose.ci-integration.yml"
export COMPOSE_ENV_FILES="ops/tests/integration.env"
BE="http://127.0.0.1:${IT_BACKEND_PORT:-18000}"; BD="http://127.0.0.1:${IT_BIDDER_PORT:-18090}"
LOG="/tmp/cdsp-it-$COMPOSE_PROJECT_NAME"; mkdir -p "$LOG"

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   $1"; }
bad() { fail=$((fail+1)); echo "  FAIL $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/       /'; }
finish() {
  docker compose logs --no-color > "$LOG/compose.log" 2>&1 || true
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap finish EXIT

# volume uploads заранее root-owned с «старым» креативом — как после апгрейда с
# root-образа. Volume создаёт сам compose (с его labels), потом портим владельца.
docker compose create --no-build backend > "$LOG/create.log" 2>&1 || { bad "compose create backend" "$(cat "$LOG/create.log")"; exit 1; }
VOL="$(docker compose config --format json | python3 -c 'import sys,json; print(json.load(sys.stdin)["volumes"]["uploads_data"]["name"])')"
docker run --rm --entrypoint sh -v "$VOL:/data/uploads" centraldsp/backend:local -c \
  'mkdir -p /data/uploads && printf "\211PNG\r\n\032\n legacy" > /data/uploads/legacy.png && chown -R 0:0 /data/uploads' >/dev/null

if ! docker compose up -d --no-build --wait --wait-timeout 180 postgres redis backend bidder-go > "$LOG/up.log" 2>&1; then
  bad "compose up --wait (healthchecks)" "$(tail -40 "$LOG/up.log"; docker compose ps)"
  echo "integration_smoke: $pass passed, $fail failed"; exit 1
fi
ok "stack up: postgres, redis, backend, bidder-go healthy"

# ---------------------------------------------------------------- backend
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BE/health")"
[ "$code" = 200 ] && ok "backend /health 200" || bad "backend /health -> $code"
code="$(curl -s -o "$LOG/ready.json" -w '%{http_code}' --max-time 10 "$BE/ready")"
[ "$code" = 200 ] && ok "backend /ready 200 (migrations applied, PG+Redis reachable)" || bad "backend /ready -> $code" "$(cat "$LOG/ready.json")"

uid_line="$(docker compose exec -T backend sh -c 'grep ^Uid: /proc/1/status; grep ^NoNewPrivs: /proc/1/status; grep ^CapEff: /proc/1/status')"
if grep -qE "^Uid:\s+10001\s+10001\s+10001\s+10001" <<<"$uid_line" && grep -qE "^NoNewPrivs:\s+1" <<<"$uid_line" \
   && grep -qE "^CapEff:\s+0+$" <<<"$uid_line"; then ok "backend PID 1 runs as uid 10001, no caps, no_new_privs"; else bad "backend PID 1 identity" "$uid_line"; fi
uid_sched="$(docker compose exec -T backend sh -c 'stat -c %u:%g /data/uploads /data/uploads/legacy.png')"
if [ "$(sort -u <<<"$uid_sched")" = "10001:10001" ]; then ok "legacy root-owned uploads volume re-owned to 10001:10001"; else bad "uploads volume owner" "$uid_sched"; fi

# --- upload flow: register (первый пользователь = superadmin) -> upload -> GET -> DELETE
# Cookie сессии всегда Secure; curl не отдаст её по http://127.0.0.1 из jar,
# поэтому берём токен из Set-Cookie и передаём заголовком явно.
code="$(curl -s -o "$LOG/register.json" -D "$LOG/register.hdr" -w '%{http_code}' --max-time 10 -H 'Content-Type: application/json' \
  -d '{"email":"ci-admin@ci.centraldsp.dev","password":"Ci-Smoke-Pass-2026!","name":"CI"}' "$BE/auth/register")"
SESSION="$(sed -n 's/^[Ss]et-[Cc]ookie: *\(cdsp_session=[^;]*\).*/\1/p' "$LOG/register.hdr" | head -n1)"
if { [ "$code" = 200 ] || [ "$code" = 201 ]; } && [ -n "$SESSION" ]; then ok "register bootstrap superadmin ($code), session cookie issued"; else bad "register -> $code (cookie: '${SESSION:-none}')" "$(cat "$LOG/register.json")"; fi
printf '\211PNG\r\n\032\n\000\000\000\015IHDR\000\000\000\001\000\000\000\001\010\006\000\000\000\037\025\304\211\000\000\000\012IDAT\170\234\143\000\001\000\000\005\000\001\015\012\055\264\000\000\000\000IEND\256\102\140\202' > "$LOG/px.png"
code="$(curl -s -o "$LOG/upload.json" -w '%{http_code}' --max-time 15 -H "Cookie: $SESSION" -F "file=@$LOG/px.png;type=image/png" "$BE/creatives/upload")"
if [ "$code" = 200 ]; then
  path="$(python3 -c 'import sys,json; print(json.load(open(sys.argv[1]))["path"])' "$LOG/upload.json")"
  ok "creative upload 200 -> $path"
  code2="$(curl -s -o "$LOG/dl.png" -w '%{http_code}' --max-time 5 "$BE$path")"
  if [ "$code2" = 200 ] && cmp -s "$LOG/dl.png" "$LOG/px.png"; then ok "uploaded creative readable byte-for-byte"; else bad "GET uploaded -> $code2"; fi
  code3="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BE/uploads/legacy.png")"
  [ "$code3" = 200 ] && ok "legacy creative (pre-upgrade file) readable" || bad "GET legacy.png -> $code3"
  name="${path##*/}"
  code4="$(curl -s -o "$LOG/del.txt" -w '%{http_code}' --max-time 5 -H "Cookie: $SESSION" -X DELETE "$BE/creatives/upload/$name")"
  [ "$code4" = 204 ] && ok "delete uploaded file 204" || bad "DELETE upload -> $code4" "$(cat "$LOG/del.txt")"
  code5="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BE$path")"
  [ "$code5" = 404 ] && ok "deleted file is gone (404)" || bad "after delete GET -> $code5 (expected 404)"
else
  bad "creative upload -> $code" "$(cat "$LOG/upload.json")"
fi
# ---------------------------------------------------------------- bidder-go
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BD/livez")"
[ "$code" = 200 ] && ok "bidder /livez 200" || bad "bidder /livez -> $code"
code="$(curl -s -o "$LOG/healthz.txt" -w '%{http_code}' --max-time 10 "$BD/healthz")"
[ "$code" = 200 ] && ok "bidder /healthz 200 (redis + feed)" || bad "bidder /healthz -> $code" "$(cat "$LOG/healthz.txt")"
code="$(curl -s -o "$LOG/bid.txt" -w '%{http_code}' --max-time 5 -H 'Content-Type: application/json' -H 'x-openrtb-version: 2.5' \
  -d '{"id":"ci-1","imp":[{"id":"1","banner":{"w":300,"h":250}}],"site":{"domain":"example.com"},"device":{"ua":"ci","ip":"203.0.113.1"}}' \
  "$BD/rtb/bid/ci-unknown-endpoint")"
case "$code" in 2??|4??) ok "bidder RTB request answered without 5xx ($code)" ;; *) bad "bidder RTB -> $code" "$(cat "$LOG/bid.txt")" ;; esac
uid_b="$(docker inspect -f '{{.Config.User}}' "$(docker compose ps -q bidder-go)")"
[ -n "$uid_b" ] && [ "$uid_b" != root ] && [ "$uid_b" != 0 ] && ok "bidder-go image user: $uid_b (non-root)" || bad "bidder-go runs as root/unspecified user: '$uid_b'"

# ---------------------------------------------------------------- graceful stop
t0="$(date +%s)"; docker compose stop -t 30 backend >/dev/null 2>&1; t1="$(date +%s)"
exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$(docker compose ps -aq backend)")"
if [ "$exit_code" = 0 ] && [ $((t1-t0)) -lt 30 ]; then ok "backend graceful stop: exit 0 in $((t1-t0))s (no SIGKILL)"; else bad "backend stop: exit=$exit_code in $((t1-t0))s"; fi

echo "integration_smoke: $pass passed, $fail failed (logs: $LOG)"
[ "$fail" = 0 ]
