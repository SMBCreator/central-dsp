#!/usr/bin/env bash
# Предпродовая проверка на VPS. Выполняет серверные (V) пункты чеклиста
# docs/audit/preprod_checklist_2026-09.md в правильном порядке и печатает
# PASS/FAIL по каждому. По умолчанию ТОЛЬКО ЧИТАЕТ (dry-run миграций,
# GET-запросы, SELECT). Мутирующие шаги — только с флагами:
#
#   ops/preprod_check.sh                 # read-only проверки
#   ops/preprod_check.sh --apply         # + backup.sh, migrate.py, /cron/retention
#   ops/preprod_check.sh --load          # + rtb_load.py по бюджету p99 (см. LOAD_*)
#   HOST=https://dsp.example.com ops/preprod_check.sh
#
# Переменные окружения (или из .env рядом с docker-compose.yml):
#   HOST           публичный URL бэкенда (default http://localhost:8000)
#   BIDDER_HOST    URL Go-биддера        (default http://localhost:8080)
#   RTB_API_TOKEN, CRON_SECRET           берутся из .env, если не заданы
#   LOAD_QPS / LOAD_SECONDS / LOAD_P99_MS  (default 500 / 60 / 30)
#   LOAD_ENDPOINT_KEY / LOAD_PARTNER_TOKEN  ключ и токен SSP-партнёра для нагрузки
#
# Выход: 0 — все проверки PASS, 1 — есть FAIL. Ничего не удаляет.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
APPLY=0; LOAD=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --load) LOAD=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "неизвестный флаг: $a" >&2; exit 2 ;;
  esac
done

# .env — источник секретов для проверок; не печатаем значения.
if [ -f .env ]; then
  set -a; # shellcheck disable=SC1091
  . ./.env; set +a
fi
HOST="${HOST:-http://localhost:8000}"
BIDDER_HOST="${BIDDER_HOST:-http://localhost:8080}"
COMPOSE="${COMPOSE:-docker compose}"
FAILS=0; PASSES=0
pass() { PASSES=$((PASSES+1)); printf '  \e[32mPASS\e[0m %s\n' "$1"; }
fail() { FAILS=$((FAILS+1));   printf '  \e[31mFAIL\e[0m %s\n' "$1"; }
skip() {                        printf '  \e[33mSKIP\e[0m %s\n' "$1"; }
section() { printf '\n== %s ==\n' "$1"; }
psql_q() { $COMPOSE exec -T postgres psql -U "${POSTGRES_USER:-dsp}" -d "${POSTGRES_DB:-dsp}" -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }
code_of() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@"; }

section "1. Секреты не дефолтные (6.1)"
for v in SESSION_SECRET EVENT_TOKEN_SECRET RTB_API_TOKEN CRON_SECRET; do
  val="${!v:-}"
  if [ -z "$val" ]; then fail "$v не задан"
  elif printf '%s' "$val" | grep -qiE '^(change_?me|changeme|replace_me|todo|xxx)'; then fail "$v — плейсхолдер"
  elif [ "${#val}" -lt 32 ]; then fail "$v короче 32 символов"
  else pass "$v задан (${#val} симв.)"; fi
done
case "${REGISTRATION_MODE:-}" in
  open) fail "REGISTRATION_MODE=open — в проде регистрация должна быть закрыта/по инвайту" ;;
  "") skip "REGISTRATION_MODE не задан в .env — проверить дефолт в backend/config" ;;
  *) pass "REGISTRATION_MODE=$REGISTRATION_MODE" ;;
esac

section "2. Сервисы живы"
for pair in "backend|$HOST/health" "bidder-go|$BIDDER_HOST/healthz" "bidder-go metrics|$BIDDER_HOST/metrics"; do
  name="${pair%%|*}"; url="${pair#*|}"
  c=$(code_of "$url"); [ "$c" = "200" ] && pass "$name $url -> 200" || fail "$name $url -> $c"
done

section "3. Миграции (6.1: dry-run, порядок наката)"
DRY=$($COMPOSE exec -T backend python migrate.py --dry-run 2>&1); rc=$?
if [ $rc -ne 0 ]; then fail "migrate.py --dry-run упал: $(echo "$DRY" | tail -2)"
elif echo "$DRY" | grep -qiE 'mismatch|checksum|sha256 differs|MODIFIED'; then fail "migrate.py: изменённые уже применённые файлы — см. вывод"; echo "$DRY" | tail -5
else
  PENDING=$(echo "$DRY" | grep -ciE 'would apply|pending|apply ' || true)
  pass "migrate.py --dry-run ок, к накату: ${PENDING} файл(ов)"
fi
if [ $APPLY -eq 1 ]; then
  echo "  -> ops/backup.sh"; if ops/backup.sh >/tmp/preprod_backup.log 2>&1; then pass "backup.sh (лог /tmp/preprod_backup.log)"; else fail "backup.sh — см. /tmp/preprod_backup.log"; fi
  echo "  -> migrate.py"; if $COMPOSE exec -T backend python migrate.py >/tmp/preprod_migrate.log 2>&1; then pass "migrate.py применён"; else fail "migrate.py — см. /tmp/preprod_migrate.log"; fi
  AFTER=$($COMPOSE exec -T backend python migrate.py --dry-run 2>&1 | grep -ciE 'would apply|pending' || true)
  [ "$AFTER" = "0" ] && pass "после наката pending=0" || fail "после наката pending=$AFTER"
else
  skip "backup.sh + migrate.py — только с --apply"
fi

section "4. Партиции bid_events и cron (6.1)"
DEF=$(psql_q "SELECT count(*) FROM ONLY bid_events_default"); [ "${DEF:-x}" = "0" ] && pass "bid_events_default пуста" || fail "bid_events_default: ${DEF:-нет таблицы} строк (алерт BidEventsDefaultPartitionNonEmpty)"
TOMORROW=$(date -u -d '+1 day' +%Y%m%d 2>/dev/null || date -u -v+1d +%Y%m%d)
HAS=$(psql_q "SELECT count(*) FROM pg_tables WHERE tablename = 'bid_events_${TOMORROW}'")
[ "${HAS:-0}" = "1" ] && pass "партиция на завтра (bid_events_${TOMORROW}) есть" || fail "нет партиции bid_events_${TOMORROW} — /cron/retention не ходит?"
if [ -n "${CRON_SECRET:-}" ]; then
  if [ $APPLY -eq 1 ]; then
    c=$(code_of -H "Authorization: Bearer $CRON_SECRET" "$HOST/cron/retention"); [ "$c" = "200" ] && pass "/cron/retention -> 200" || fail "/cron/retention -> $c"
  fi
  c=$(code_of "$HOST/cron/retention"); [ "$c" = "401" ] || [ "$c" = "403" ] && pass "/cron/* без секрета -> $c" || fail "/cron/retention без секрета -> $c (ожидали 401/403)"
fi
if (crontab -l 2>/dev/null; systemctl list-timers --all 2>/dev/null) | grep -qE 'cron/(retention|reset-daily|rules)'; then pass "cron/systemd содержит вызовы /cron/*"; else fail "в crontab/systemd timers нет /cron/retention|reset-daily|rules"; fi

section "5. Фид и RTB-вход (6.3, 6.4)"
if [ -n "${RTB_API_TOKEN:-}" ]; then
  FEED=$(curl -s --max-time 15 -H "Authorization: Bearer $RTB_API_TOKEN" "$HOST/rtb-feed/campaigns")
  N=$(printf '%s' "$FEED" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d if isinstance(d,list) else d.get("campaigns",d.get("items",[]))))' 2>/dev/null || echo "?")
  [ "$N" != "?" ] && pass "/rtb-feed/campaigns: $N кампаний в фиде" || fail "/rtb-feed/campaigns: не JSON / ошибка"
  c=$(code_of "$HOST/rtb-feed/campaigns"); [ "$c" = "401" ] || [ "$c" = "403" ] && pass "фид без токена -> $c" || fail "фид без токена -> $c"
else skip "RTB_API_TOKEN не задан — фид не проверен"; fi
c=$(code_of -X POST -H 'Content-Type: application/json' -d '{}' "$BIDDER_HOST/rtb/bid/no-such-endpoint-key"); [ "$c" = "404" ] && pass "неизвестный endpoint_key -> 404" || fail "неизвестный endpoint_key -> $c"
if [ -n "${LOAD_ENDPOINT_KEY:-}" ]; then
  c=$(code_of -X POST -H 'Content-Type: application/json' -H 'X-CentralDSP-Token: wrong' -d '{"id":"x","imp":[]}' "$BIDDER_HOST/rtb/bid/$LOAD_ENDPOINT_KEY"); [ "$c" = "403" ] && pass "неверный токен -> 403" || fail "неверный токен -> $c"
  BIG=$(head -c 300000 /dev/zero | tr '\0' 'a')
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST -H 'Content-Type: application/json' -H "X-CentralDSP-Token: ${LOAD_PARTNER_TOKEN:-x}" --data-binary "{\"id\":\"$BIG\"}" "$BIDDER_HOST/rtb/bid/$LOAD_ENDPOINT_KEY"); [ "$c" = "413" ] && pass "тело > 256 KiB -> 413" || fail "тело > 256 KiB -> $c"
else skip "LOAD_ENDPOINT_KEY не задан — 403/413 не проверены"; fi

section "6. Сходимость bid_events -> stats_hourly (6.6)"
if $COMPOSE exec -T backend python scripts/convergence_check.py --hours 24 >/tmp/preprod_convergence.log 2>&1; then pass "convergence_check --hours 24 (лог /tmp/preprod_convergence.log)"
else rc=$?; [ $rc -eq 2 ] && fail "convergence_check: ошибка подключения" || fail "convergence_check: расхождения — см. /tmp/preprod_convergence.log"; fi

section "7. Нагрузка (6.9)"
if [ $LOAD -eq 1 ]; then
  if [ -z "${LOAD_ENDPOINT_KEY:-}" ] || [ -z "${LOAD_PARTNER_TOKEN:-}" ]; then fail "--load требует LOAD_ENDPOINT_KEY и LOAD_PARTNER_TOKEN"
  else
    if $COMPOSE exec -T backend python loadtest/rtb_load.py --url "$BIDDER_HOST/rtb/bid/$LOAD_ENDPOINT_KEY" --token "$LOAD_PARTNER_TOKEN" \
         --qps "${LOAD_QPS:-500}" --seconds "${LOAD_SECONDS:-60}" --p99-budget-ms "${LOAD_P99_MS:-30}" >/tmp/preprod_load.json 2>/tmp/preprod_load.err; then
      pass "rtb_load qps=${LOAD_QPS:-500} p99<=${LOAD_P99_MS:-30}ms (отчёт /tmp/preprod_load.json)"
    else fail "rtb_load: p99 выше бюджета или ошибки — /tmp/preprod_load.json, /tmp/preprod_load.err"; fi
  fi
else skip "rtb_load — только с --load"; fi

section "8. Заголовки безопасности фронта (6.10)"
FRONT="${FRONT_HOST:-${HOST%:8000}:3000}"
H=$(curl -s -D - -o /dev/null --max-time 10 "$FRONT/login" 2>/dev/null)
for hdr in "strict-transport-security" "x-frame-options" "x-content-type-options" "content-security-policy"; do
  printf '%s' "$H" | grep -qi "^$hdr:" && pass "$hdr" || fail "$hdr отсутствует ($FRONT/login)"
done

printf '\n%d PASS, %d FAIL\n' "$PASSES" "$FAILS"
[ $FAILS -eq 0 ]
