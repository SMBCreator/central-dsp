#!/usr/bin/env bash
# Тесты ops/preprod_check.sh без Docker: подставные `docker` и `curl` в PATH
# отвечают из каталога фикстур. Проверяется контракт скрипта:
#   * вердикты/exit codes (0 PASS, 1 FAIL, 2 usage, 3 NOT TESTED);
#   * цепочка зависимостей (bidder 503 -> RTB-пробы NOT TESTED, а не FAIL);
#   * ни одной мутирующей команды (up/restart/migrate без --dry-run);
#   * нагрузка ходит через отдельный контейнер с internal-check заголовком.
#   ops/tests/preprod_check_test.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
FX="$T/fx"; REPO="$T/repo"; BIN="$T/bin"
mkdir -p "$FX/http" "$FX/curl" "$REPO/ops/lib" "$BIN"
cp "$ROOT/ops/preprod_check.sh" "$REPO/ops/"; cp "$ROOT/ops/lib/http_probe.py" "$REPO/ops/lib/"
export FX; export CALLS="$T/calls.log"; : >"$CALLS"

# ---------------------------------------------------------------- подставной docker
cat >"$BIN/docker" <<'FAKE'
#!/usr/bin/env bash
set -u
echo "$*" >>"$CALLS"
[ "${1:-}" = "compose" ] || { echo "fake docker: only compose" >&2; exit 1; }
shift
keyof() { printf '%s' "$1" | sed -E 's#^https?://##; s#[^A-Za-z0-9._-]#_#g'; }
case "$1" in
  config) exit "$(cat "$FX/compose_config.rc" 2>/dev/null || echo 0)" ;;
  ps) cat "$FX/ps.json"; exit 0 ;;
  exec|run)
    args=("$@")
    # сервис: exec -T <svc> ... | run --rm --no-deps -T <svc> ...
    svc=""; for a in "${args[@]}"; do case "$a" in exec|run|-T|--rm|--no-deps) ;; *) svc="$a"; break ;; esac; done
    case "$svc" in
      postgres)
        [ -e "$FX/pg_down" ] && { echo "psql: connection refused" >&2; exit 2; }
        sql="${args[-1]}"
        case "$sql" in
          "SELECT 1") echo 1 ;;
          *bid_events_default*) cat "$FX/pg_default_count" ;;
          *pg_tables*) cat "$FX/pg_tomorrow" ;;
          *scheduled_jobs*) cat "$FX/pg_jobs" ;;
          *supply_partners*) cat "$FX/pg_partner" ;;
          *) echo "fake psql: no fixture for $sql" >&2; exit 1 ;;
        esac; exit 0 ;;
      backend)
        rest=("${args[@]}"); while [ "${rest[0]}" != python ]; do rest=("${rest[@]:1}"); done; rest=("${rest[@]:1}")
        case "${rest[0]}" in
          migrate.py) [ "${rest[1]:-}" = "--dry-run" ] || { echo "MUTATION: migrate without --dry-run" >>"$CALLS"; exit 99; }
                      cat "$FX/migrate.out"; exit "$(cat "$FX/migrate.rc")" ;;
          scripts/convergence_check.py) cat "$FX/convergence.json"; exit "$(cat "$FX/convergence.rc")" ;;
          loadtest/rtb_load.py) printf '%s\n' "${rest[@]:1}" >"$FX/load.args"; cat "$FX/load.json"; exit "$(cat "$FX/load.rc")" ;;
          -) method="${rest[1]}"; url="${rest[2]}"; key="${method}_$(keyof "$url")"; suffix=""
             for a in "${rest[@]:3}"; do
               case "$a" in --body-size) suffix="_size" ;; *wrong-token*) suffix="_wrongtoken" ;; Authorization:*) suffix="_auth" ;; esac
             done
             [ -z "$suffix" ] && [ "$method" = POST ] && [ -e "$FX/http/${key}_bid.json" ] && suffix="_bid"
             f="$FX/http/${key}${suffix}.json"
             if [ -e "$f" ]; then cat "$f"; else printf '{"status":0,"headers":{},"body":"","body_bytes":0,"error":"no fixture %s"}\n' "${key}${suffix}"; fi
             exit 0 ;;
          *) echo "fake docker: unexpected python ${rest[*]}" >&2; exit 1 ;;
        esac ;;
      *) echo "fake docker: unexpected service $svc" >&2; exit 1 ;;
    esac ;;
  *) echo "MUTATION: docker compose $*" >>"$CALLS"; exit 99 ;;
esac
FAKE
# ---------------------------------------------------------------- подставной curl
cat >"$BIN/curl" <<'FAKE'
#!/usr/bin/env bash
echo "curl $*" >>"$CALLS"
url=""; want_code=0; want_hdrs=0
for a in "$@"; do case "$a" in http*) url="$a" ;; -w) want_code=1 ;; -D) want_hdrs=1 ;; esac; done
key=$(printf '%s' "$url" | sed -E 's#^https?://##; s#[^A-Za-z0-9._-]#_#g')
[ $want_hdrs = 1 ] && { cat "$FX/curl/$key.headers" 2>/dev/null; exit 0; }
[ $want_code = 1 ] && { cat "$FX/curl/$key.code" 2>/dev/null || printf '000'; exit 0; }
exit 0
FAKE
chmod +x "$BIN/docker" "$BIN/curl"

# ---------------------------------------------------------------- baseline «всё хорошо» (production)
baseline() {
  rm -rf "$FX"; mkdir -p "$FX/http" "$FX/curl"; : >"$CALLS"
  cat >"$REPO/.env" <<'ENV'
APP_ENV=production
COMPOSE_FILE=compose.yml:compose.prod.yml
DOMAIN=dsp.example
ACME_EMAIL=ops@example
PUBLIC_BASE_URL=https://dsp.example
POSTGRES_PASSWORD=pgpassword-not-a-placeholder
SESSION_SECRET=0123456789abcdef0123456789abcdef0123
EVENT_TOKEN_SECRET=0123456789abcdef0123456789abcdef0124
RTB_API_TOKEN=0123456789abcdef0123456789abcdef0125
CRON_SECRET=0123456789abcdef0123456789abcdef0126
REGISTRATION_MODE=closed
ENV
  jq -cn '[{Service:"postgres",State:"running",Health:"healthy"},{Service:"redis",State:"running",Health:"healthy"},
           {Service:"backend",State:"running",Health:"healthy"},{Service:"scheduler",State:"running",Health:"healthy"},
           {Service:"bidder-go",State:"running",Health:"healthy"},{Service:"frontend",State:"running",Health:"healthy"},
           {Service:"caddy",State:"running",Health:""}]' >"$FX/ps.json"
  echo 0 >"$FX/pg_default_count"; echo 1 >"$FX/pg_tomorrow"; echo key1 >"$FX/pg_partner"
  printf 'retention|t|ok||f\nstats_rollup|t|ok||f\nml_train|f|ok||f\n' >"$FX/pg_jobs"
  echo "Nothing to do: all migrations applied." >"$FX/migrate.out"; echo 0 >"$FX/migrate.rc"
  echo '{"pass":true,"hours_with_diff":0,"bid_events":{"bids":100,"wins":10,"spend":1.5}}' >"$FX/convergence.json"; echo 0 >"$FX/convergence.rc"
  http() { jq -cn --argjson s "$2" --arg b "$3" --argjson h "${4:-{\}}" '{status:$s,headers:$h,body:$b,body_bytes:($b|length),error:null}' >"$FX/http/$1.json"; }
  http GET_backend_8000_cron_retention 401 '{"detail":"unauthorized"}'
  http GET_backend_8000_ready 200 '{"status":"ok","checks":{"postgres":"ok","redis":"ok","events_consumer":"ok"}}'
  http GET_backend_8000_rtb-feed_campaigns_auth 200 '{"version":"v7","candidates":[{"id":1},{"id":2}]}'
  http GET_backend_8000_rtb-feed_campaigns 401 '{"detail":"bad feed token"}'
  http GET_bidder-go_8090_healthz 200 '{"status":"ok","campaigns":2,"partners":1,"feed_age_sec":4,"redis":true}'
  http GET_bidder-go_8090_metrics 200 'rtb_requests_total 1'
  http POST_bidder-go_8090_rtb_bid_no-such-endpoint-key-preprod 404 'unknown endpoint'
  http POST_bidder-go_8090_rtb_bid_key1_wrongtoken 403 'forbidden'
  http POST_bidder-go_8090_rtb_bid_key1_size 413 'request too large'
  http POST_bidder-go_8090_rtb_bid_key1_bid 204 ''
  http GET_frontend_3000_login 200 '<html>' '{"x-frame-options":"DENY","x-content-type-options":"nosniff","content-security-policy-report-only":"default-src"}'
  echo 200 >"$FX/curl/dsp.example_api_health.code"; echo 404 >"$FX/curl/dsp.example_rtb_bid_no-such-endpoint-key-preprod.code"
  printf 'HTTP/2 200\r\nstrict-transport-security: max-age=63072000\r\nx-content-type-options: nosniff\r\nx-frame-options: DENY\r\ncontent-security-policy: default-src self\r\n\r\n' >"$FX/curl/dsp.example_login.headers"
  echo '{"result":"PASS","exit_code":0,"target_qps":500,"achieved_qps":500,"accepted_qps":500,"latency_ms":{"p99":4.2},"responses":{"429":0},"error_rate":0,"bid_rate":0.4,"reason":"ok"}' >"$FX/load.json"; echo 0 >"$FX/load.rc"
}

PASSED=0; FAILED=0; OUT="$T/out.txt"
run_check() { (cd "$REPO" && PATH="$BIN:$PATH" FEED_PARITY_GRACE=0 PREPROD_LOG_DIR="$T/logs" ops/preprod_check.sh "$@") >"$OUT" 2>&1; echo $?; }
expect() { # name expected_rc actual_rc [grep-pattern...]
  local name="$1" want="$2" got="$3"; shift 3; local ok=1
  [ "$want" = "$got" ] || { echo "  ожидали exit $want, получили $got"; ok=0; }
  for pat in "$@"; do grep -qE -- "$pat" "$OUT" || { echo "  в выводе нет: $pat"; ok=0; }; done
  if [ $ok = 1 ]; then PASSED=$((PASSED+1)); echo "ok   $name"; else FAILED=$((FAILED+1)); echo "FAIL $name"; sed 's/^/    | /' "$OUT" | tail -40; fi
}

baseline; rc=$(run_check --json "$T/report.json")
expect "production, всё зелёное -> exit 0" 0 "$rc" 'VERDICT: PASS' 'паритет фида' 'тело 300000 байт -> 413' 'валидный BidRequest.*204' 'enforcing' 'strict-transport-security'
if grep -q MUTATION "$CALLS" || grep -qE ' (up|down|restart|stop|start|pull) ' "$CALLS"; then FAILED=$((FAILED+1)); echo "FAIL мутирующие команды:"; grep -E 'MUTATION| (up|down|restart|stop|start|pull) ' "$CALLS"; else PASSED=$((PASSED+1)); echo "ok   ни одной мутирующей docker-команды"; fi
if [ "$(jq -r '.verdict + " " + (.exit_code|tostring) + " " + (.counts.fail|tostring)' "$T/report.json")" = "PASS 0 0" ] && [ "$(jq '.checks|length' "$T/report.json")" -gt 20 ]; then PASSED=$((PASSED+1)); echo "ok   --json отчёт"; else FAILED=$((FAILED+1)); echo "FAIL --json отчёт: $(head -c 300 "$T/report.json")"; fi

baseline; rc=$(run_check --apply)
expect "--apply больше нет -> exit 2" 2 "$rc" 'флага --apply больше нет'

baseline; rc=$(run_check --bogus)
expect "неизвестный флаг -> exit 2" 2 "$rc"

baseline; jq -c '.status=503 | .body="{\"status\":\"not_ready\",\"checks\":{\"postgres\":\"ok\",\"redis\":\"error\",\"events_consumer\":\"not_running\"}}"' "$FX/http/GET_backend_8000_ready.json" >"$T/h" && mv "$T/h" "$FX/http/GET_backend_8000_ready.json"
rc=$(run_check)
expect "backend /ready 503 -> FAIL с разбивкой причин (audit O-05)" 1 "$rc" 'FAIL +backend /ready' 'redis=error' 'events_consumer=not_running'

baseline; jq -c '.status=503 | .body="{\"status\":\"feed_stale\",\"campaigns\":0}"' "$FX/http/GET_bidder-go_8090_healthz.json" >"$T/h" && mv "$T/h" "$FX/http/GET_bidder-go_8090_healthz.json"
rc=$(run_check)
expect "bidder healthz 503 -> FAIL, RTB-пробы NOT TESTED (не FAIL)" 1 "$rc" 'FAIL +bidder /healthz' 'NOT TESTED +RTB-пробы' 'NOT TESTED +rtb_load|SKIP +rtb_load'

baseline; printf 'retention|t|never||f\nstats_rollup|t|ok||f\n' >"$FX/pg_jobs"; rc=$(run_check)
expect "job ещё не выполнялся -> NOT TESTED, exit 3" 3 "$rc" 'NOT TESTED +job retention' 'VERDICT: NOT TESTED'

baseline; printf 'retention|t|error|HTTP 500: boom|f\nstats_rollup|t|ok||t\n' >"$FX/pg_jobs"; rc=$(run_check)
expect "job error и просроченный next_run_at -> FAIL" 1 "$rc" 'FAIL +job retention.*HTTP 500' 'FAIL +job stats_rollup.*просрочен'

baseline; printf 'pending  054_new.sql\npending  055_more.sql\n' >"$FX/migrate.out"; rc=$(run_check)
expect "pending миграции -> FAIL с отсылкой к runbook" 1 "$rc" 'FAIL +migrate.py --dry-run — pending=2.*runbook'

baseline; echo 4 >"$FX/migrate.rc"; echo "ERROR: checksum mismatch" >"$FX/migrate.out"; rc=$(run_check)
expect "migrate rc=4 (изменён применённый файл) -> FAIL" 1 "$rc" 'FAIL +migrate.py.*sha256'

baseline; echo 3 >"$FX/migrate.rc"; rc=$(run_check)
expect "migrate rc=3 (lock занят) -> NOT TESTED" 3 "$rc" 'NOT TESTED +migrate.py.*lock'

baseline; jq -c '.body="{\"status\":\"ok\",\"campaigns\":5,\"partners\":1,\"feed_age_sec\":4,\"redis\":true}"' "$FX/http/GET_bidder-go_8090_healthz.json" >"$T/h" && mv "$T/h" "$FX/http/GET_bidder-go_8090_healthz.json"; rc=$(run_check)
expect "паритет фида нарушен -> FAIL" 1 "$rc" 'FAIL +паритет фида — backend candidates=2, bidder campaigns=5'

baseline; jq -c '.body="{\"version\":\"v7\"}"' "$FX/http/GET_backend_8000_rtb-feed_campaigns_auth.json" >"$T/h" && mv "$T/h" "$FX/http/GET_backend_8000_rtb-feed_campaigns_auth.json"; rc=$(run_check)
expect "фид 200 без candidates -> FAIL (не считаем 200 успехом)" 1 "$rc" 'FAIL +/rtb-feed/campaigns — HTTP 200, но тело без массива candidates'

baseline; echo 2 >"$FX/convergence.rc"; echo '{}' >"$FX/convergence.json"; rc=$(run_check)
expect "convergence rc=2 -> NOT TESTED, exit 3" 3 "$rc" 'NOT TESTED +convergence_check'

baseline; echo 1 >"$FX/convergence.rc"; echo '{"pass":false,"hours_with_diff":3}' >"$FX/convergence.json"; rc=$(run_check)
expect "convergence rc=1 -> FAIL" 1 "$rc" 'FAIL +convergence_check.*3 час'

baseline; printf 'HTTP/2 200\r\nx-content-type-options: nosniff\r\nx-frame-options: DENY\r\ncontent-security-policy-report-only: default-src\r\n\r\n' >"$FX/curl/dsp.example_login.headers"; rc=$(run_check)
expect "CSP Report-Only -> PASS с пометкой, без HSTS -> FAIL" 1 "$rc" 'PASS +content-security-policy — Report-Only' 'FAIL +strict-transport-security'

baseline; sed -i 's/^REGISTRATION_MODE=closed/REGISTRATION_MODE=open/' "$REPO/.env"; rc=$(run_check)
expect "REGISTRATION_MODE=open -> FAIL" 1 "$rc" 'FAIL +REGISTRATION_MODE'

baseline; sed -i 's/^SESSION_SECRET=.*/SESSION_SECRET=changeme/' "$REPO/.env"; rc=$(run_check)
expect "плейсхолдер секрета -> FAIL" 1 "$rc" 'FAIL +SESSION_SECRET — плейсхолдер'

baseline; jq -c 'map(if .Service=="backend" then .State="exited" | .Health="" else . end)' "$FX/ps.json" >"$T/p" && mv "$T/p" "$FX/ps.json"; rc=$(run_check)
expect "backend не running -> FAIL контейнера, зависимые NOT TESTED, пробы bidder через run --rm" 1 "$rc" 'FAIL +backend — state=exited' 'NOT TESTED +migrate.py' 'PASS +bidder /healthz'
grep -q 'compose run --rm --no-deps -T backend python - GET http://bidder-go:8090/healthz' "$CALLS" && { PASSED=$((PASSED+1)); echo "ok   без backend пробы идут через одноразовый контейнер"; } || { FAILED=$((FAILED+1)); echo "FAIL ожидали compose run для пробы"; grep healthz "$CALLS"; }

baseline; rc=$(run_check --load)
expect "--load PASS -> exit 0, сводка из отчёта" 0 "$rc" 'PASS +rtb_load — target=500 achieved=500 accepted=500 p99=4.2ms 429=0'
if grep -q 'X-CentralDSP-Internal-Check: 0123456789abcdef0123456789abcdef0125' "$FX/load.args" && grep -q '^http://bidder-go:8090/rtb/bid/key1$' "$FX/load.args" && grep -q 'run --rm --no-deps -T backend python loadtest/rtb_load.py' "$CALLS"; then PASSED=$((PASSED+1)); echo "ok   нагрузка: отдельный контейнер, URL bidder-go, internal-check заголовок"; else FAILED=$((FAILED+1)); echo "FAIL аргументы нагрузки:"; cat "$FX/load.args"; fi

baseline; echo 3 >"$FX/load.rc"; echo '{"result":"NOT TESTED","exit_code":3,"target_qps":35000,"achieved_qps":18200,"accepted_qps":18200,"latency_ms":{"p99":3},"responses":{"429":0},"error_rate":0,"bid_rate":0,"reason":"генератор насыщен"}' >"$FX/load.json"; rc=$(run_check --load)
expect "--load NOT TESTED (генератор насыщен) -> exit 3" 3 "$rc" 'NOT TESTED +rtb_load — target=35000 achieved=18200.*генератор насыщен'

baseline; echo 1 >"$FX/load.rc"; echo '{"result":"FAIL","exit_code":1,"target_qps":500,"achieved_qps":500,"accepted_qps":480,"latency_ms":{"p99":3},"responses":{"429":20},"error_rate":0,"bid_rate":0,"reason":"gate no_429"}' >"$FX/load.json"; rc=$(run_check --load)
expect "--load с 429 -> FAIL" 1 "$rc" 'FAIL +rtb_load.*429=20'

baseline; sed -i -e '/^APP_ENV/d' -e '/^COMPOSE_FILE/d' -e '/^PUBLIC_BASE_URL/d' -e '/^DOMAIN/d' -e '/^ACME_EMAIL/d' "$REPO/.env"
jq -c 'map(select(.Service!="caddy"))' "$FX/ps.json" >"$T/p" && mv "$T/p" "$FX/ps.json"; rc=$(run_check)
expect "dev-режим: prod-only SKIP, заголовки через frontend, HSTS SKIP -> exit 0" 0 "$rc" 'SKIP +APP_ENV' 'PASS +content-security-policy — Report-Only \(frontend:3000/login' 'SKIP +strict-transport-security' 'SKIP +публичный вход через Caddy' 'VERDICT: PASS'

baseline; sed -i '/^PUBLIC_BASE_URL/d' "$REPO/.env"; rc=$(run_check)
expect "production без PUBLIC_BASE_URL -> FAIL" 1 "$rc" 'FAIL +PUBLIC_BASE_URL'

printf '\n%d ok, %d failed\n' "$PASSED" "$FAILED"
[ "$FAILED" -eq 0 ]
