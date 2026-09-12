#!/usr/bin/env bash
# Предпродовый release gate на VPS: серверные (V) пункты чеклиста
# docs/audit/preprod_checklist_2026-09.md, в порядке зависимостей. ТОЛЬКО ЧИТАЕТ:
# GET/SELECT, migrate.py --dry-run, тестовые POST в биддер, которые он отвергает
# (404/403/413) или обрабатывает как OpenRTB test=1 (без резерва и списаний).
# Накат (backup -> migrate -> restart -> reload) — вручную по runbook
# docs/runbooks/release.md §2; у скрипта нет режима, который что-то применяет.
#
#   ops/preprod_check.sh                       # все проверки
#   ops/preprod_check.sh --load                # + нагрузка rtb_load.py (создаёт test-события у партнёра)
#   ops/preprod_check.sh --json /tmp/report.json
#
# Каждая проверка получает один из вердиктов:
#   PASS        проверено, критерий выполнен
#   FAIL        проверено, критерий НЕ выполнен
#   NOT TESTED  проверить не удалось (зависимость недоступна, нет данных, ошибка инструмента)
#   SKIP        проверка не запрашивалась (--load) или не применима в этом окружении (dev)
# Exit code:
#   0  все обязательные проверки PASS (SKIP допустим)
#   1  есть хотя бы один FAIL
#   2  ошибка использования/окружения: нет docker/jq/curl, нет .env, неизвестный флаг
#   3  FAIL нет, но есть NOT TESTED — gate не доказан, релиз не разрешён
#
# Переменные (из .env рядом с compose.yml или окружения; значения не печатаются):
#   APP_ENV, COMPOSE_FILE, PUBLIC_BASE_URL, DOMAIN, POSTGRES_USER/DB,
#   SESSION_SECRET, EVENT_TOKEN_SECRET, RTB_API_TOKEN, CRON_SECRET, REGISTRATION_MODE
#   LOAD_ENDPOINT_KEY   endpoint_key SSP-партнёра для 403/413/bid-проб и нагрузки
#                       (default: первый партнёр active/testing из БД)
#   LOAD_PARTNER_TOKEN  открытый X-CentralDSP-Token партнёра (иначе Skip-Token через internal check)
#   LOAD_QPS / LOAD_SECONDS / LOAD_WORKERS / LOAD_P99_MS / LOAD_TMAX_MS  параметры rtb_load (см. --help там)
#   FEED_PARITY_GRACE   сек. ожидания перед повторной сверкой фида backend<->bidder (default 10)
#   PREPROD_LOG_DIR     куда писать логи инструментов (default /tmp/preprod)
set -uo pipefail

usage() { sed -n '2,36p' "$0"; }

LOAD=0; JSON_OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --load) LOAD=1 ;;
    --json) shift; JSON_OUT="${1:-}"; [ -n "$JSON_OUT" ] || { echo "--json требует путь" >&2; exit 2; } ;;
    --apply)
      echo "ошибка: флага --apply больше нет. Скрипт только проверяет; накат — по docs/runbooks/release.md §2" >&2
      exit 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "неизвестный флаг: $1 (см. --help)" >&2; exit 2 ;;
  esac
  shift
done

cd "$(dirname "$0")/.." || exit 2
for cmd in docker jq curl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ошибка: нужен $cmd" >&2; exit 2; }
done
[ -f .env ] || { echo "ошибка: нет .env рядом с compose.yml — проверять нечего" >&2; exit 2; }
set -a; # shellcheck disable=SC1091
. ./.env; set +a

LOG_DIR="${PREPROD_LOG_DIR:-/tmp/preprod}"; mkdir -p "$LOG_DIR" || exit 2
IS_PROD=0; [ "${APP_ENV:-}" = "production" ] && IS_PROD=1
case "${COMPOSE_FILE:-}" in *compose.prod.yml*) HAS_CADDY=1 ;; *) HAS_CADDY=0 ;; esac
PGUSER="${POSTGRES_USER:-centraldsp}"; PGDB="${POSTGRES_DB:-centraldsp}"
PUBLIC_URL="${HOST:-${PUBLIC_BASE_URL:-}}"; PUBLIC_URL="${PUBLIC_URL%/}"

# ---------------------------------------------------------------- вердикты
N_PASS=0; N_FAIL=0; N_SKIP=0; N_NT=0
CHECKS_NDJSON="$(mktemp)"; trap 'rm -f "$CHECKS_NDJSON"' EXIT
SECTION=""
section() { SECTION="$1"; printf '\n== %s ==\n' "$1"; }
_record() { # verdict name detail
  jq -cn --arg s "$SECTION" --arg v "$1" --arg n "$2" --arg d "$3" \
    '{section:$s, verdict:$v, check:$n, detail:$d}' >>"$CHECKS_NDJSON"
}
if [ -t 1 ]; then C_G=$'\e[32m'; C_R=$'\e[31m'; C_Y=$'\e[33m'; C_M=$'\e[35m'; C_0=$'\e[0m'; else C_G=""; C_R=""; C_Y=""; C_M=""; C_0=""; fi
_line() { printf '  %s%-10s%s %s%s\n' "$1" "$2" "$C_0" "$3" "${4:+ — $4}"; }
pass() { N_PASS=$((N_PASS+1)); _line "$C_G" "PASS" "$1" "${2:-}"; _record PASS "$1" "${2:-}"; }
fail() { N_FAIL=$((N_FAIL+1)); _line "$C_R" "FAIL" "$1" "${2:-}"; _record FAIL "$1" "${2:-}"; }
skip() { N_SKIP=$((N_SKIP+1)); _line "$C_Y" "SKIP" "$1" "${2:-}"; _record SKIP "$1" "${2:-}"; }
nt()   { N_NT=$((N_NT+1));     _line "$C_M" "NOT TESTED" "$1" "${2:-}"; _record "NOT TESTED" "$1" "${2:-}"; }

# ---------------------------------------------------------------- инструменты
dc() { docker compose "$@"; }

# psql_q SQL -> stdout результат (tuples-only), rc = rc psql. Пустой результат
# при rc=0 — легитимный ответ (например, count=0), при rc!=0 — недоступность.
psql_q() { dc exec -T postgres psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 -tAqc "$1" 2>>"$LOG_DIR/psql.err"; }

# http METHOD URL [probe args] -> JSON пробы (см. ops/lib/http_probe.py).
# Выполняется в контейнере backend (compose-сеть); если backend не запущен —
# в одноразовом контейнере из его образа, чтобы проверки bidder/frontend не
# зависели от backend.
BACKEND_UP=0
http() {
  local out
  if [ "$BACKEND_UP" = 1 ]; then
    out=$(dc exec -T backend python - "$@" <ops/lib/http_probe.py 2>>"$LOG_DIR/probe.err")
  else
    out=$(dc run --rm --no-deps -T backend python - "$@" <ops/lib/http_probe.py 2>>"$LOG_DIR/probe.err")
  fi
  if [ -z "$out" ] || ! printf '%s' "$out" | jq -e . >/dev/null 2>&1; then
    printf '{"status":0,"headers":{},"body":"","body_bytes":0,"error":"probe did not run (docker compose exec/run failed, see %s/probe.err)"}' "$LOG_DIR"
  else
    printf '%s' "$out"
  fi
}
jget() { printf '%s' "$1" | jq -r "$2" 2>/dev/null; }
# Ответ пробы -> человекочитаемая причина недоступности.
why() { local e; e=$(jget "$1" '.error // empty'); [ -n "$e" ] && printf '%s' "$e" || printf 'HTTP %s' "$(jget "$1" .status)"; }

BACKEND_IN="http://backend:8000"; BIDDER_IN="http://bidder-go:8090"; FRONT_IN="http://frontend:3000"

# ================================================================ 1. окружение
section "1. Окружение и секреты"
[ $IS_PROD -eq 1 ] && pass "APP_ENV=production" || skip "APP_ENV=${APP_ENV:-<пусто>}" "не production — prod-only проверки (Caddy, PUBLIC_BASE_URL) пропущены"
for v in SESSION_SECRET EVENT_TOKEN_SECRET RTB_API_TOKEN CRON_SECRET POSTGRES_PASSWORD; do
  val="${!v:-}"
  if [ -z "$val" ]; then fail "$v" "не задан"
  elif printf '%s' "$val" | grep -qiE '^(change_?me|changeme|replace_me|todo|xxx|secret|password)'; then fail "$v" "плейсхолдер"
  elif [ "${#val}" -lt 32 ] && [ "$v" != POSTGRES_PASSWORD ]; then fail "$v" "короче 32 символов (${#val})"
  else pass "$v" "задан (${#val} симв.)"; fi
done
case "${REGISTRATION_MODE:-}" in
  open) fail "REGISTRATION_MODE" "open — в проде регистрация закрыта/по инвайту" ;;
  ""|closed) pass "REGISTRATION_MODE" "${REGISTRATION_MODE:-не задан -> default closed}" ;;
  *) fail "REGISTRATION_MODE" "«$REGISTRATION_MODE» не распознан (open|closed)" ;;
esac
if [ $IS_PROD -eq 1 ]; then
  [ $HAS_CADDY -eq 1 ] && pass "COMPOSE_FILE включает compose.prod.yml" || fail "COMPOSE_FILE" "production без compose.prod.yml: нет Caddy/TLS, порты открыты на хосте"
  [ -n "${DOMAIN:-}" ] && [ -n "${ACME_EMAIL:-}" ] && pass "DOMAIN/ACME_EMAIL заданы" || fail "DOMAIN/ACME_EMAIL" "нужны Caddy для сертификата"
  case "$PUBLIC_URL" in
    https://*) pass "PUBLIC_BASE_URL" "$PUBLIC_URL" ;;
    "") fail "PUBLIC_BASE_URL" "не задан — публичные проверки (Caddy, заголовки, /rtb через прокси) невозможны" ;;
    *) fail "PUBLIC_BASE_URL" "$PUBLIC_URL — в production ожидается https://" ;;
  esac
fi

# ================================================================ 2. контейнеры
section "2. Контейнеры Compose"
PG_UP=0; BIDDER_UP=0; FRONT_UP=0; SCHED_UP=0
if dc config -q >"$LOG_DIR/compose_config.log" 2>&1; then
  pass "docker compose config" "конфигурация собирается"
  PS=$(dc ps --all --format json 2>>"$LOG_DIR/compose.err")
  # compose v2 печатает либо JSON-массив, либо NDJSON — нормализуем в массив.
  PS=$(printf '%s' "$PS" | jq -cs 'if length == 1 and (.[0]|type) == "array" then .[0] else . end' 2>/dev/null || echo '[]')
  if [ "$(printf '%s' "$PS" | jq 'length')" = "0" ]; then
    fail "docker compose ps" "ни одного контейнера — стек не запущен"
  else
    EXPECTED="postgres redis backend scheduler bidder-go frontend"; [ $HAS_CADDY -eq 1 ] && EXPECTED="$EXPECTED caddy"
    for svc in $EXPECTED; do
      row=$(printf '%s' "$PS" | jq -c --arg s "$svc" 'map(select(.Service == $s)) | first // empty')
      state=$(jget "$row" '.State // "absent"'); health=$(jget "$row" '.Health // ""')
      if [ "$state" = "running" ] && { [ -z "$health" ] || [ "$health" = "healthy" ]; }; then
        pass "$svc" "running${health:+ ($health)}"
        case "$svc" in postgres) PG_UP=1 ;; backend) BACKEND_UP=1 ;; bidder-go) BIDDER_UP=1 ;; frontend) FRONT_UP=1 ;; scheduler) SCHED_UP=1 ;; esac
      else
        fail "$svc" "state=$state${health:+ health=$health}"
      fi
    done
  fi
else
  fail "docker compose config" "см. $LOG_DIR/compose_config.log"
fi

# ================================================================ 3. postgres и миграции
section "3. Postgres и миграции"
PG_OK=0
if [ $PG_UP -eq 1 ]; then
  if [ "$(psql_q 'SELECT 1')" = "1" ]; then PG_OK=1; pass "psql SELECT 1" "$PGUSER@$PGDB"
  else fail "psql SELECT 1" "нет доступа к $PGDB под $PGUSER — см. $LOG_DIR/psql.err"; fi
else nt "psql SELECT 1" "контейнер postgres не running"; fi

if [ $PG_OK -eq 1 ] && [ $BACKEND_UP -eq 1 ]; then
  DRY=$(dc exec -T backend python migrate.py --dry-run 2>&1); rc=$?
  printf '%s\n' "$DRY" >"$LOG_DIR/migrate_dry_run.log"
  PENDING=$(printf '%s\n' "$DRY" | grep -cE '^pending  ' || true)
  case $rc in
    0) if [ "$PENDING" = "0" ]; then pass "migrate.py --dry-run" "все миграции применены (pending=0)"
       else fail "migrate.py --dry-run" "pending=$PENDING — неприменённые миграции; накат по runbook §2, затем повторить проверку"; fi ;;
    3) nt "migrate.py --dry-run" "advisory lock занят — идёт другой прогон миграций" ;;
    4) fail "migrate.py --dry-run" "изменён уже применённый файл миграции (sha256 mismatch) — см. $LOG_DIR/migrate_dry_run.log" ;;
    *) fail "migrate.py --dry-run" "rc=$rc — см. $LOG_DIR/migrate_dry_run.log" ;;
  esac
else nt "migrate.py --dry-run" "нужны postgres и backend"; fi

# ================================================================ 4. партиции и планировщик
section "4. Партиции bid_events и планировщик"
if [ $PG_OK -eq 1 ]; then
  DEF=$(psql_q "SELECT count(*) FROM ONLY bid_events_default"); rc=$?
  if [ $rc -ne 0 ]; then nt "bid_events_default" "запрос не выполнился (нет таблицы?)"
  elif [ "$DEF" = "0" ]; then pass "bid_events_default пуста"
  else fail "bid_events_default" "$DEF строк — события мимо партиций (алерт BidEventsDefaultPartitionNonEmpty)"; fi
  TOMORROW=$(date -u -d '+1 day' +%Y%m%d 2>/dev/null || date -u -v+1d +%Y%m%d)
  HAS=$(psql_q "SELECT count(*) FROM pg_tables WHERE tablename = 'bid_events_${TOMORROW}'"); rc=$?
  if [ $rc -ne 0 ]; then nt "партиция bid_events_${TOMORROW}" "запрос не выполнился"
  elif [ "$HAS" = "1" ]; then pass "партиция bid_events_${TOMORROW} создана"
  else fail "партиция bid_events_${TOMORROW}" "нет — job retention не отработал"; fi

  JOBS=$(psql_q "SELECT name, enabled, coalesce(last_status,'never'), coalesce(left(last_error,120),''), (enabled AND next_run_at IS NOT NULL AND next_run_at < now() - interval '15 minutes') FROM scheduled_jobs ORDER BY name"); rc=$?
  if [ $rc -ne 0 ]; then nt "scheduled_jobs" "таблица недоступна (миграция 053 не применена?)"
  elif [ -z "$JOBS" ]; then fail "scheduled_jobs" "таблица пуста — планировщик не засеян"
  else
    while IFS='|' read -r name enabled status err overdue; do
      [ -n "$name" ] || continue
      if [ "$enabled" != "t" ]; then
        if [ -n "$err" ]; then fail "job $name" "выключен планировщиком: $err"; else skip "job $name" "выключен оператором"; fi
      elif [ "$overdue" = "t" ]; then fail "job $name" "next_run_at просрочен >15 мин — scheduler не забирает задания"
      else
        case "$status" in
          ok) pass "job $name" "последний запуск ok" ;;
          never) nt "job $name" "ещё ни разу не выполнялся" ;;
          *) fail "job $name" "last_status=$status${err:+: $err}" ;;
        esac
      fi
    done <<<"$JOBS"
  fi
else
  nt "bid_events_default" "postgres недоступен"; nt "партиция на завтра" "postgres недоступен"; nt "scheduled_jobs" "postgres недоступен"
fi
[ $SCHED_UP -eq 1 ] || nt "scheduler tick" "контейнер scheduler не running/healthy"

if [ $BACKEND_UP -eq 1 ]; then
  R=$(http GET "$BACKEND_IN/cron/retention")
  case "$(jget "$R" .status)" in
    401|403) pass "/cron/* без секрета" "HTTP $(jget "$R" .status)" ;;
    0) nt "/cron/* без секрета" "$(why "$R")" ;;
    *) fail "/cron/* без секрета" "HTTP $(jget "$R" .status), ожидали 401/403" ;;
  esac
else nt "/cron/* без секрета" "backend не running"; fi

# Readiness backend (audit O-05): /health — только liveness (его смотрит Docker),
# /ready — PostgreSQL, Redis, consumer rtb:events; 503 с кодами по каждой проверке.
if [ $BACKEND_UP -eq 1 ]; then
  R=$(http GET "$BACKEND_IN/ready")
  st=$(jget "$R" .status)
  CH=$(jget "$R" '.body|fromjson|.checks|to_entries|map("\(.key)=\(.value)")|join(", ")')
  [ -n "$CH" ] || CH=$(jget "$R" '.body[0:160]')
  case "$st" in
    200) pass "backend /ready" "HTTP 200: $CH" ;;
    0) nt "backend /ready" "$(why "$R")" ;;
    *) fail "backend /ready" "HTTP $st: $CH" ;;
  esac
else nt "backend /ready" "backend не running"; fi

# ================================================================ 5. фид и RTB-вход
section "5. Фид backend -> bidder и RTB-вход"
FEED_N=""
if [ $BACKEND_UP -eq 1 ] && [ -n "${RTB_API_TOKEN:-}" ]; then
  R=$(http GET "$BACKEND_IN/rtb-feed/campaigns" -H "Authorization: Bearer $RTB_API_TOKEN" --timeout 20)
  st=$(jget "$R" .status)
  if [ "$st" = "200" ]; then
    FEED_N=$(jget "$R" '.body | fromjson | .candidates | if type == "array" then length else empty end')
    FEED_V=$(jget "$R" '.body | fromjson | .version // empty')
    if [ -n "$FEED_N" ]; then pass "/rtb-feed/campaigns" "HTTP 200, candidates=$FEED_N${FEED_V:+, version=$FEED_V}"
    else fail "/rtb-feed/campaigns" "HTTP 200, но тело без массива candidates: $(jget "$R" '.body[0:120]')"; fi
  elif [ "$st" = "0" ]; then nt "/rtb-feed/campaigns" "$(why "$R")"
  else fail "/rtb-feed/campaigns" "HTTP $st: $(jget "$R" '.body[0:120]')"; fi
  R=$(http GET "$BACKEND_IN/rtb-feed/campaigns")
  case "$(jget "$R" .status)" in
    401|403) pass "фид без токена" "HTTP $(jget "$R" .status)" ;;
    0) nt "фид без токена" "$(why "$R")" ;;
    *) fail "фид без токена" "HTTP $(jget "$R" .status), ожидали 401" ;;
  esac
else nt "/rtb-feed/campaigns" "нужны backend и RTB_API_TOKEN"; nt "фид без токена" "нужны backend и RTB_API_TOKEN"; fi

BIDDER_TRADES=0
if [ $BIDDER_UP -eq 1 ]; then
  H=$(http GET "$BIDDER_IN/healthz")
  st=$(jget "$H" .status)
  if [ "$st" = "200" ]; then
    BIDDER_TRADES=1
    pass "bidder /healthz" "HTTP 200: status=$(jget "$H" '.body|fromjson|.status'), campaigns=$(jget "$H" '.body|fromjson|.campaigns'), partners=$(jget "$H" '.body|fromjson|.partners'), feed_age=$(jget "$H" '.body|fromjson|.feed_age_sec')s"
    [ "$(jget "$H" '.body|fromjson|.redis')" = "true" ] && pass "bidder redis" "ping ok" || fail "bidder redis" "недоступен — нет freq cap/резервов бюджета"
    if [ -n "$FEED_N" ]; then
      BN=$(jget "$H" '.body|fromjson|.campaigns')
      if [ "$BN" != "$FEED_N" ]; then
        sleep "${FEED_PARITY_GRACE:-10}"
        H2=$(http GET "$BIDDER_IN/healthz"); BN=$(jget "$H2" '.body|fromjson|.campaigns // empty')
      fi
      [ "$BN" = "$FEED_N" ] && pass "паритет фида" "backend candidates=$FEED_N == bidder campaigns=$BN" \
        || fail "паритет фида" "backend candidates=$FEED_N, bidder campaigns=${BN:-?} после ${FEED_PARITY_GRACE:-10}с — bidder не подтянул фид"
    else nt "паритет фида" "нет числа candidates из backend"; fi
  elif [ "$st" = "0" ]; then nt "bidder /healthz" "$(why "$H")"
  else fail "bidder /healthz" "HTTP $st: $(jget "$H" '.body[0:160]')"; fi
  M=$(http GET "$BIDDER_IN/metrics")
  [ "$(jget "$M" .status)" = "200" ] && pass "bidder /metrics" "HTTP 200, $(jget "$M" .body_bytes) байт" || fail "bidder /metrics" "$(why "$M")"
else
  nt "bidder /healthz" "контейнер bidder-go не running"; nt "bidder redis" "bidder-go не running"; nt "паритет фида" "bidder-go не running"; nt "bidder /metrics" "bidder-go не running"
fi

# Пробы RTB-входа. Зависят от того, что биддер торгует: при 503 (feed stale)
# он отвечает 503 до auth и любые ожидания 404/403/413 станут ложными FAIL.
KEY="${LOAD_ENDPOINT_KEY:-}"
if [ -z "$KEY" ] && [ $PG_OK -eq 1 ]; then
  KEY=$(psql_q "SELECT endpoint_key FROM supply_partners WHERE status IN ('active','testing') ORDER BY (status='active') DESC, created_at LIMIT 1") || KEY=""
fi
if [ $BIDDER_TRADES -eq 1 ]; then
  R=$(http POST "$BIDDER_IN/rtb/bid/no-such-endpoint-key-preprod" --body '{"id":"x","imp":[]}')
  [ "$(jget "$R" .status)" = "404" ] && pass "неизвестный endpoint_key -> 404" || fail "неизвестный endpoint_key" "$(why "$R"), ожидали 404"
  if [ -n "$KEY" ]; then
    R=$(http POST "$BIDDER_IN/rtb/bid/$KEY" -H "X-CentralDSP-Token: wrong-token-preprod" --body '{"id":"x","imp":[{"id":"1"}]}')
    [ "$(jget "$R" .status)" = "403" ] && pass "неверный токен -> 403" "партнёр $KEY" || fail "неверный токен" "$(why "$R"), ожидали 403"
    # Auth для положительных проб: открытый токен партнёра, иначе internal check
    # + Skip-Token (как автопроверка из кабинета, ssp_check.py).
    AUTH=(); AUTH_KIND=""
    if [ -n "${LOAD_PARTNER_TOKEN:-}" ]; then AUTH=(-H "X-CentralDSP-Token: $LOAD_PARTNER_TOKEN"); AUTH_KIND="токен партнёра"
      [ -n "${RTB_API_TOKEN:-}" ] && AUTH+=(-H "X-CentralDSP-Internal-Check: $RTB_API_TOKEN")
    elif [ -n "${RTB_API_TOKEN:-}" ]; then AUTH=(-H "X-CentralDSP-Internal-Check: $RTB_API_TOKEN" -H "X-CentralDSP-Skip-Token: 1"); AUTH_KIND="internal check + Skip-Token"; fi
    if [ ${#AUTH[@]} -gt 0 ]; then
      # 413: тело > MaxBidRequestBytes (256 KiB), собирается пробой в памяти, не через argv.
      R=$(http POST "$BIDDER_IN/rtb/bid/$KEY" "${AUTH[@]}" --body-size 300000)
      st=$(jget "$R" .status)
      if [ "$st" = "413" ]; then pass "тело 300000 байт -> 413" "$AUTH_KIND"
      elif [ "$st" = "403" ]; then nt "тело 300000 байт -> 413" "403 ($(jget "$R" '.body|rtrimstr("\n")')) — auth не пройден: $AUTH_KIND"
      else fail "тело 300000 байт -> 413" "$(why "$R")"; fi
      BODY='{"id":"preprod-'"$(date +%s)"'","test":1,"tmax":120,"at":1,"cur":["RUB"],"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":0.1,"bidfloorcur":"RUB"}],"site":{"id":"preprod","domain":"example.ru","page":"https://example.ru/"},"device":{"ua":"preprod-check","ip":"10.0.0.1","devicetype":2},"user":{"id":"preprod"}}'
      R=$(http POST "$BIDDER_IN/rtb/bid/$KEY" "${AUTH[@]}" --body "$BODY")
      st=$(jget "$R" .status)
      case "$st" in
        204) pass "валидный BidRequest (test=1) -> 204 no-bid" "кандидатов на этот запрос нет; путь парсинг->таргетинг исправен" ;;
        200)
          RID=$(jget "$R" '.body|fromjson|.id // empty'); NB=$(jget "$R" '.body|fromjson|[.seatbid[]?.bid[]?]|length')
          if [ -n "$RID" ] && [ "${NB:-0}" -gt 0 ]; then pass "валидный BidRequest (test=1) -> 200" "bids=$NB, id echo ok, cur=$(jget "$R" '.body|fromjson|.cur')"
          else fail "валидный BidRequest (test=1) -> 200" "тело не похоже на BidResponse: $(jget "$R" '.body[0:160]')"; fi ;;
        0) nt "валидный BidRequest (test=1)" "$(why "$R")" ;;
        *) fail "валидный BidRequest (test=1)" "HTTP $st: $(jget "$R" '.body[0:160]')" ;;
      esac
    else nt "413 и валидный BidRequest" "нет ни LOAD_PARTNER_TOKEN, ни RTB_API_TOKEN для auth"; fi
  else nt "403/413/валидный BidRequest" "нет партнёра active/testing (LOAD_ENDPOINT_KEY не задан, в БД не найден)"; fi
else
  nt "RTB-пробы (404/403/413/bid)" "bidder не торгует (healthz != 200) или недоступен"
fi

# ================================================================ 6. сходимость
section "6. Сходимость bid_events -> stats_hourly"
if [ $PG_OK -eq 1 ] && [ $BACKEND_UP -eq 1 ]; then
  dc exec -T backend python scripts/convergence_check.py --hours 24 --json >"$LOG_DIR/convergence.json" 2>"$LOG_DIR/convergence.err"; rc=$?
  case $rc in
    0) pass "convergence_check --hours 24" "$(jq -r '"bids=\(.bid_events.bids|floor) wins=\(.bid_events.wins|floor) spend=\(.bid_events.spend)"' "$LOG_DIR/convergence.json" 2>/dev/null)" ;;
    1) fail "convergence_check --hours 24" "расхождения в $(jq -r .hours_with_diff "$LOG_DIR/convergence.json" 2>/dev/null) час(ах) — $LOG_DIR/convergence.json" ;;
    2) nt "convergence_check --hours 24" "$(head -c 200 "$LOG_DIR/convergence.err")" ;;
    *) nt "convergence_check --hours 24" "rc=$rc — $LOG_DIR/convergence.err" ;;
  esac
else nt "convergence_check" "нужны postgres и backend"; fi

# ================================================================ 7. нагрузка
section "7. Нагрузка на bidder (rtb_load.py)"
if [ $LOAD -eq 0 ]; then skip "rtb_load" "только с --load"
elif [ $BIDDER_TRADES -ne 1 ]; then nt "rtb_load" "bidder не торгует"
elif [ -z "$KEY" ]; then nt "rtb_load" "нет endpoint_key партнёра"
else
  LARGS=(--url "$BIDDER_IN/rtb/bid/$KEY" --qps "${LOAD_QPS:-500}" --seconds "${LOAD_SECONDS:-60}" --workers "${LOAD_WORKERS:-2}"
         --p99-budget-ms "${LOAD_P99_MS:-30}" --tmax-ms "${LOAD_TMAX_MS:-120}")
  [ -n "${LOAD_PARTNER_TOKEN:-}" ] && LARGS+=(--token "$LOAD_PARTNER_TOKEN")
  # Генератор ходит из compose-сети, а не с IP партнёра: internal check
  # пропускает IP-whitelist (Skip-Token — только если открытого токена нет).
  if [ -n "${RTB_API_TOKEN:-}" ]; then
    LARGS+=(--header "X-CentralDSP-Internal-Check: $RTB_API_TOKEN")
    [ -z "${LOAD_PARTNER_TOKEN:-}" ] && LARGS+=(--header "X-CentralDSP-Skip-Token: 1")
  fi
  # Отдельный контейнер из образа backend: не отнимает CPU у обслуживающего
  # backend и видит bidder-go по имени. CPU генератора всё равно делится с
  # биддером на одном VPS — отчёт содержит его загрузку.
  dc run --rm --no-deps -T backend python loadtest/rtb_load.py "${LARGS[@]}" >"$LOG_DIR/load.json" 2>"$LOG_DIR/load.err"; rc=$?
  SUM=$(jq -r '"target=\(.target_qps) achieved=\(.achieved_qps) accepted=\(.accepted_qps) p99=\(.latency_ms.p99)ms 429=\(.responses["429"]) err=\(.error_rate) bid_rate=\(.bid_rate)"' "$LOG_DIR/load.json" 2>/dev/null || echo "отчёт не разобран")
  case $rc in
    0) pass "rtb_load" "$SUM ($LOG_DIR/load.json)" ;;
    1) fail "rtb_load" "$SUM; причина: $(jq -r '.reason // ""' "$LOG_DIR/load.json" 2>/dev/null) ($LOG_DIR/load.json)" ;;
    3) nt "rtb_load" "$SUM; $(jq -r '.reason // ""' "$LOG_DIR/load.json" 2>/dev/null)" ;;
    *) nt "rtb_load" "rc=$rc (ошибка запуска/конфигурации) — $LOG_DIR/load.err" ;;
  esac
fi

# ================================================================ 8. публичный вход и заголовки
section "8. Публичный вход (Caddy) и заголовки безопасности"
check_headers() { # $1 = raw headers, $2 = источник, $3 = hsts_expected(0/1)
  local hdrs="$1" src="$2" want_hsts="$3" h
  for h in x-content-type-options x-frame-options; do
    printf '%s' "$hdrs" | grep -qi "^$h:" && pass "$h" "$src" || fail "$h" "отсутствует ($src)"
  done
  if printf '%s' "$hdrs" | grep -qi '^content-security-policy:'; then pass "content-security-policy" "enforcing ($src)"
  elif printf '%s' "$hdrs" | grep -qi '^content-security-policy-report-only:'; then pass "content-security-policy" "Report-Only ($src) — допустимо на текущем этапе (prod_readiness R6), enforcing после разбора отчётов"
  else fail "content-security-policy" "ни CSP, ни CSP-Report-Only ($src)"; fi
  if [ "$want_hsts" = 1 ]; then
    printf '%s' "$hdrs" | grep -qi '^strict-transport-security:' && pass "strict-transport-security" "$src" || fail "strict-transport-security" "отсутствует ($src) — ставит Caddy"
  else skip "strict-transport-security" "выставляет Caddy; без PUBLIC_BASE_URL не проверяется"; fi
}
if [ -n "$PUBLIC_URL" ]; then
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$PUBLIC_URL/api/health" 2>>"$LOG_DIR/curl.err")
  case "$code" in
    200) pass "$PUBLIC_URL/api/health" "HTTP 200 через Caddy" ;;
    000) nt "$PUBLIC_URL/api/health" "нет соединения/TLS — $LOG_DIR/curl.err" ;;
    *) fail "$PUBLIC_URL/api/health" "HTTP $code" ;;
  esac
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 -X POST -H 'Content-Type: application/json' -d '{"id":"x","imp":[]}' "$PUBLIC_URL/rtb/bid/no-such-endpoint-key-preprod" 2>>"$LOG_DIR/curl.err")
  case "$code" in
    404) pass "$PUBLIC_URL/rtb/bid/* -> bidder" "HTTP 404 (unknown endpoint от bidder-go)" ;;
    000) nt "$PUBLIC_URL/rtb/bid/*" "нет соединения" ;;
    503) fail "$PUBLIC_URL/rtb/bid/*" "HTTP 503 — bidder не торгует (feed)" ;;
    *) fail "$PUBLIC_URL/rtb/bid/*" "HTTP $code, ожидали 404 от bidder-go" ;;
  esac
  HDRS=$(curl -sS -D - -o /dev/null --max-time 15 "$PUBLIC_URL/login" 2>>"$LOG_DIR/curl.err")
  if [ -n "$HDRS" ]; then check_headers "$HDRS" "$PUBLIC_URL/login" 1
  else nt "заголовки безопасности" "$PUBLIC_URL/login не ответил"; fi
elif [ $FRONT_UP -eq 1 ]; then
  R=$(http GET "$FRONT_IN/login")
  if [ "$(jget "$R" .status)" != "0" ]; then
    HDRS=$(jget "$R" '.headers | to_entries[] | "\(.key): \(.value)"')
    check_headers "$HDRS" "frontend:3000/login (без Caddy)" 0
  else nt "заголовки безопасности" "$(why "$R")"; fi
  skip "публичный вход через Caddy" "PUBLIC_BASE_URL не задан (dev)"
else
  nt "заголовки безопасности" "frontend не running и нет PUBLIC_BASE_URL"
fi

# ================================================================ итог
printf '\n%d PASS, %d FAIL, %d NOT TESTED, %d SKIP\n' "$N_PASS" "$N_FAIL" "$N_NT" "$N_SKIP"
if [ $N_FAIL -gt 0 ]; then VERDICT=FAIL; EXIT=1
elif [ $N_NT -gt 0 ]; then VERDICT="NOT TESTED"; EXIT=3
else VERDICT=PASS; EXIT=0; fi
printf 'VERDICT: %s (exit %d)%s\n' "$VERDICT" "$EXIT" "$([ $EXIT -eq 3 ] && printf ' — gate не доказан: устраните причины NOT TESTED и повторите')"
if [ -n "$JSON_OUT" ]; then
  jq -cs --arg v "$VERDICT" --argjson e "$EXIT" --argjson prod "$IS_PROD" --argjson load "$LOAD" \
    '{verdict:$v, exit_code:$e, production:($prod==1), load:($load==1),
      counts:{pass:(map(select(.verdict=="PASS"))|length), fail:(map(select(.verdict=="FAIL"))|length),
              not_tested:(map(select(.verdict=="NOT TESTED"))|length), skip:(map(select(.verdict=="SKIP"))|length)},
      checks:.}' "$CHECKS_NDJSON" >"$JSON_OUT"
fi
exit $EXIT
