# Reality check — ветка `feat/avito-ui-kktu` (2026-08-31)

Срез фактического состояния перед итерацией «Часть A (роли) + Часть D (observability)».
Факты получены из кода и прогонов тестов, не из коммит-сообщений.

## Baseline тестов (до изменений)

- `backend`: **104 passed, 6 failed, 7 skipped** — все 6 падений в `tests/test_freq_caps.py`
  из-за ограничения fakeredis (Lua-скрипты `EVALSHA` частично не эмулируются). Падения
  существовали до итерации и не связаны с её изменениями. С живым Redis тесты проходят.
- `bidder-go`: Go toolchain в среде отсутствовал; тесты гоняются в CI.

## Часть A — что реально было в ветке до итерации

| Утверждение плана | Факт в коде |
| --- | --- |
| Роль `viewer` в `rbac.py` | **Отсутствовала** — `ALL_ROLES` без viewer |
| `PERMISSION_FLAGS` (4 флага) | **Отсутствовали** — только `can_edit_campaigns`, `can_edit_settings`, `can_manage_billing` в `users_router.ALLOWED_PERMISSIONS`, из них реально применялся один |
| `has_permission()` | **Отсутствовал** |
| `require_share_manager` и гейт `share.py` | **Отсутствовал** — создание share-ссылок и гостей было доступно любому авторизованному, включая analyst |
| `permissions` в `GET /auth/me` | **Отсутствовали** — фронт не мог гейтить UI по флагам |
| `frontend/lib/roles.ts` c `ROUTE_ACCESS`/`homeRoute`/`canWrite` | **Отсутствовал** — был только `ADMIN_ROUTES` + `isAdminRole` (грубое деление админ/не-админ) |
| `frontend/lib/use-me.ts` | **Отсутствовал** |
| Страница `/report` для viewer | **Отсутствовала** |

## Часть B/C (ML, throttling) — что реально было

- `backend/ml/` — **нет каталога**, CatBoost в зависимостях **отсутствует**.
- Пре-фильтры трафика/throttling, datacenter-списки — **нет кода** ни в `backend`, ни в `bidder-go`.
- Вне объёма текущей итерации (следующие итерации).

## Часть D — что реально было

- В Go-биддере метрики Prometheus **не оформлены отдельным пакетом**; GAP-011 в
  `docs/audit/gap_register.md` открыт.
- Alert rules / runbook — отсутствовали.

## Что сделано в этой итерации (Часть A)

- `rbac.py`: `viewer` в `ALL_ROLES`; `PERMISSION_FLAGS` (`can_edit_campaigns`,
  `can_manage_creatives`, `can_view_billing`, `can_share_stats`); `has_permission()` с
  легаси-алиасом `can_manage_billing → can_view_billing`; зависимости
  `require_share_manager`, `require_creative_manager`, `require_settings_write`,
  `require_billing_viewer`.
- `routers/share.py`: create/revoke ссылок и гостей за `require_share_manager`.
- `routers/creatives.py`: write-эндпоинты за `require_creative_manager`.
- `routers/billing.py`: просмотр транзакций/summary за `require_billing_viewer`
  (деньги проводит по-прежнему только `FINANCE_WRITE`).
- `routers/settings.py`: PATCH настроек и domain-lists за `require_settings_write`.
- `routers/auth_router.py`: `GET /auth/me` возвращает эффективные `permissions`.
- `routers/users_router.py`: роль `viewer` создаваемая; 4 актуальных флага; легаси-ключи
  нормализуются при чтении и записи.
- `migrations/025_role_viewer.sql`: users_role_check расширен ролью `viewer`.
- Фронтенд: `lib/roles.ts` (матрица `ROUTE_ACCESS`, `canAccessRoute`, `homeRoute`,
  `canWrite`), `lib/use-me.ts` (SWR-хук `/auth/me`), гейт app-shell/sidebar по матрице,
  редирект по роли после логина, страница `/report` для viewer, гейт write-кнопок в
  кампаниях/креативах, обновлённый `/users`.
- Тесты: `tests/test_rbac_matrix.py` — 60+ параметризованных проверок матрицы A1;
  обновлён `test_all_roles_covered`.

## Результаты после изменений

- `backend`: **183 passed, 6 failed (те же pre-existing freq_caps), 7 skipped** — регрессий нет.
- `frontend`: `tsc --noEmit` — чисто.

## Незакрытые риски

- E2E-прогон ролей в браузере против живого стека (Postgres+Redis) в песочнице не
  выполнялся; серверная матрица покрыта unit-тестами, фронтовая — компиляцией.
- 6 падений `test_freq_caps` требуют live Redis в CI либо замены Lua-пути на
  эмулируемый — вне объёма итерации.
