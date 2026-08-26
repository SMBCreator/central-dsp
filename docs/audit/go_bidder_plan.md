# Go Bidder — план имплементации и rollout

Решение владельца продукта (2026-08-26): **Go — основной трек биддера**.
Java (`bidder/`) заморожен как reference. Python RTB — источник истины
поведения + dev/симулятор, в проде выключен (`RTB_PYTHON_SERVING=false`).

## Почему Go

- p99 latency и footprint: компилируемый бинарь ~15 MB (distroless), нет JVM
  warm-up/GC-пауз уровня Java, нет GIL/интерпретатора Python.
- Java-биддер (форк RTB4FREE) имеет структурные долги: деньги в double USD,
  CAS-циклы вместо атомарных Lua, нет audiences/erid/PMP — доводка дороже
  переписывания по готовой спеке.
- Один бинарь = простой canary/rollback.

## Фаза 1 — скелет (сделано, `d051afa`)

- `cmd/bidder`: HTTP-сервер (net/http, Go 1.25), graceful shutdown, `/healthz`.
- `internal/rtb`: модели OpenRTB 2.5 (вкл. `user.eids` для UID2/ID5),
  хэндлеры bid/win/loss/imp; skeleton всегда отвечает 204 no-bid.
- `internal/feed`: загрузчик кампаний, контракт `/internal/bidder-feed`
  + `X-Internal-Token` — тот же, что у Java.
- `internal/targeting`: домены (suffix, blacklist wins) и geo — с тестами.
- `internal/shading`: `ShouldShade` (паритет GAP-005), фактор — TODO.
- `internal/budget`, `internal/freq`: спеки в package docs.
- Dockerfile, compose-сервис за `--profile bidder-go`.

## Фаза 2 — паритет (следующая итерация)

Порядок — по денежному риску:

1. **budget**: перенос Lua `gate_and_reserve`/`reserve_budget` из
   `backend/cache.py` 1:1 (go-redis Script). Копейки int64. Fail-closed.
2. **freq**: multi-window Lua (до 3 окон атомарно). Fail-open check / fail-safe bump.
3. **Полный bid path**: targeting (OS/device/SSP-allowlist/audiences с
   ifa→eids fallback), PMP deals, цена в RUB, floor*1.01, erid в `bid.ext`.
4. **shading.GetFactor**: чтение `shading:{campaign}:{partner}` из Redis
   с локальным кэшем ~10s (обучение остаётся в Python cron).
5. **win/loss/imp**: идемпотентный win (SETNX) → вызов backend billing;
   loss → release резерва.
6. **Метрики**: Prometheus с первого дня — bids, wins, no-bid причины,
   latency histogram, feed staleness, budget reserve failures.

## Фаза 3 — contract-тесты

Единый корпус bid request'ов (JSON-фикстуры) прогоняется через Python
(`process_bid_request`) и Go: ответы должны совпадать по решению
(bid/no-bid), цене (с точностью до копейки) и erid. Расхождение = блокер.

## Фаза 4 — rollout

1. **Shadow** (stage → prod): зеркалирование трафика в Go без учёта ответа;
   сравнение решений/цен/латентности ≥ 3 дня. Критерий: расхождений по
   деньгам 0, p99 < 50ms.
2. **Canary 5%** прод-трафика ≥ 3 дня. Критерий: win rate и spend в пределах
   ±5% от базовой линии, нет ошибок биллинга.
3. **Cutover 100%**, Java-контейнер остановлен (код удаляется после 2 недель
   стабильности — GAP-018), `RTB_PYTHON_SERVING=false` в prod.
4. **Rollback-план**: compose-профиль обратно на Java/Python — один деплой.
