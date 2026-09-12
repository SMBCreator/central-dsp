#!/usr/bin/env bash
# Сверка РЕАЛЬНЫХ cgroup-лимитов внутри контейнеров с compose (audit O-07 / C6).
# `docker compose config` показывает намерение; этот скрипт читает memory.max и
# cpu.max (cgroup v2) через `docker inspect` HostConfig и через /sys/fs/cgroup
# в самом контейнере, где есть shell. Нужен после `up` с compose.limits.yml и
# как CI/runtime check: если Compose в данном режиме не применил deploy.resources —
# задача с лимитами НЕ закрыта.
#
# Exit codes (единые для ops/*): 0 PASS, 1 FAIL (лимит задан, но cgroup другой),
# 2 usage/config, 3 NOT TESTED (контейнер не запущен / нет данных).
#   ops/check_cgroup_limits.sh [--limits-file compose.limits.yml] [service ...]
set -uo pipefail

LIMITS_FILE="compose.limits.yml"
SERVICES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --limits-file) LIMITS_FILE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 2 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) SERVICES+=("$1"); shift ;;
  esac
done
command -v docker >/dev/null || { echo "docker not found" >&2; exit 2; }
[ -f "$LIMITS_FILE" ] || { echo "limits file $LIMITS_FILE not found" >&2; exit 2; }

# Ожидания берём из отрендеренного compose config (учитывает .env и все overlays).
if ! CONF="$(docker compose config --format json 2>/tmp/cgroup_check_err)"; then
  echo "docker compose config failed:" >&2; cat /tmp/cgroup_check_err >&2; exit 2
fi
if [ "${#SERVICES[@]}" = 0 ]; then
  mapfile -t SERVICES < <(python3 -c 'import sys,json; [print(k) for k,v in json.load(sys.stdin)["services"].items() if v.get("deploy",{}).get("resources",{}).get("limits")]' <<<"$CONF")
fi
[ "${#SERVICES[@]}" -gt 0 ] || { echo "NOT TESTED: no service has deploy.resources.limits in rendered config (is $LIMITS_FILE in COMPOSE_FILE?)"; exit 3; }

rc=0; not_tested=0
for svc in "${SERVICES[@]}"; do
  want_mem="$(python3 -c 'import sys,json; s=json.load(sys.stdin)["services"][sys.argv[1]]; print(s.get("deploy",{}).get("resources",{}).get("limits",{}).get("memory",""))' "$svc" <<<"$CONF")"
  want_cpus="$(python3 -c 'import sys,json; s=json.load(sys.stdin)["services"][sys.argv[1]]; print(s.get("deploy",{}).get("resources",{}).get("limits",{}).get("cpus",""))' "$svc" <<<"$CONF")"
  cid="$(docker compose ps -q "$svc" 2>/dev/null | head -n1)"
  if [ -z "$cid" ]; then echo "NOT TESTED $svc: container not running"; not_tested=1; continue; fi
  # HostConfig — что Docker реально передал в cgroup.
  got_mem="$(docker inspect -f '{{.HostConfig.Memory}}' "$cid")"
  got_nano="$(docker inspect -f '{{.HostConfig.NanoCpus}}' "$cid")"
  # compose config уже нормализовал memory в байты (число) — сравниваем как числа.
  if [ -n "$want_mem" ]; then
    if [ "$got_mem" = "$want_mem" ]; then echo "PASS $svc memory: $got_mem bytes"; else echo "FAIL $svc memory: cgroup=$got_mem compose=$want_mem"; rc=1; fi
  fi
  if [ -n "$want_cpus" ]; then
    want_nano="$(python3 -c 'import sys; print(int(round(float(sys.argv[1])*1e9)))' "$want_cpus")"
    if [ "$got_nano" = "$want_nano" ]; then echo "PASS $svc cpus: $want_cpus"; else echo "FAIL $svc cpus: cgroup NanoCpus=$got_nano compose=$want_nano"; rc=1; fi
  fi
  # Второй источник правды — сам контейнер (cgroup v2), если там есть shell.
  inner="$(docker exec "$cid" sh -c 'cat /sys/fs/cgroup/memory.max 2>/dev/null; echo; cat /sys/fs/cgroup/cpu.max 2>/dev/null' 2>/dev/null || true)"
  if [ -n "$inner" ]; then
    inner_mem="$(printf '%s\n' "$inner" | sed -n 1p)"
    if [ -n "$want_mem" ] && [ "$inner_mem" != "$want_mem" ] && [ "$inner_mem" != "max" ]; then
      echo "FAIL $svc memory.max inside container=$inner_mem compose=$want_mem"; rc=1
    elif [ -n "$want_mem" ] && [ "$inner_mem" = "max" ]; then
      echo "FAIL $svc memory.max inside container is 'max' (no limit applied)"; rc=1
    fi
  else
    echo "INFO $svc: no shell in image, in-container cgroup read skipped (HostConfig checked)"
  fi
done
if [ "$rc" = 1 ]; then echo "VERDICT: FAIL"; exit 1; fi
if [ "$not_tested" = 1 ]; then echo "VERDICT: NOT TESTED"; exit 3; fi
echo "VERDICT: PASS"; exit 0
