# Спецификация scope: роль × сущность × операция (S-02 / S-03, план B12)

Единственный источник фильтра владельцев в API — `rbac.scope_for(user)` (CRUD, billing)
и `rbac.report_scope_for(user)` (отчёты). Правила ниже проверяются тестом
`backend/tests/test_scope_matrix_db.py` (матрица list → detail → allowed action на своих и
чужих объектах) и `test_idor_isolation_db.py` (два manager-а не видят друг друга).

## 1. Scope владельцев по ролям

| Роль | `scope_for` (CRUD, billing) | Права на запись | Отчёты (`report_scope_for`) |
|---|---|---|---|
| superadmin, admin | вся платформа (`all=True`) | полные (guards `require_*`) | как CRUD (B12b) |
| analyst | вся платформа | нет — 403 от guard, **не** 404 от scope | как CRUD (B12b) |
| finance | вся платформа | только `/billing/deposit`, `/billing/adjust` | как CRUD (B12b) |
| agency | сам + всё поддерево клиентов (`parent_user_id`, рекурсивно) | CAMPAIGN_WRITE | те же владельцы, spend × наценка цепочки |
| manager, advertiser | только свои объекты | manager: CAMPAIGN_WRITE + PLATFORM_WRITE; advertiser: CAMPAIGN_WRITE | свои |
| client | только свои объекты | по флагам `permissions` | свои, spend × наценка цепочки |
| viewer | **пусто** — CRUD-ручки отдают `[]`/404 | нет | владелец из `guest_access.owner_id`, граница `advertiser_id`, spend × `markup_mult_snapshot` |

Инварианты:

* list, detail, update, delete одной сущности используют один и тот же `Scope`. Если объект
  виден в списке — он открывается в карточке; если не виден — везде 404 (не 403: не
  раскрываем существование чужого объекта).
* Read-only роли отличаются от «не найдено»: analyst/finance на мутации получают 403 от
  `require_*` до обращения к БД.
* `Scope.where(column, n)` разворачивается в `($n::boolean OR column = ANY($n+1::uuid[]))` —
  один и тот же предикат для всех запросов, параметры `Scope.params`.

## 2. Владелец создаваемого объекта

Дочерний объект принадлежит **владельцу родителя**, а не текущему пользователю:
admin/agency, создавая клиенту кампанию, позицию, креатив или контрагента, не забирают
объект себе.

| Создаём | Родитель (должен быть в scope) | `user_id` нового объекта |
|---|---|---|
| campaign | advertiser | `advertiser.user_id` |
| line_item | campaign | `campaign.user_id` |
| creative | campaign | `campaign.user_id` |
| counterparty c `advertiser_id` | advertiser | `advertiser.user_id` |
| counterparty без `advertiser_id` | — | текущий пользователь |
| ord_contract | client counterparty | `client.user_id` |
| advertiser, campaign_group, scoring_profile, pixel | — | текущий пользователь |

## 3. Ссылочные поля body (S-02)

Каждый foreign id в create/patch должен принадлежать **владельцу главного объекта**
(`rbac.assert_owned_by` / `assert_refs_owned`). Это строже, чем «в scope пользователя»:
admin, у которого в scope и alice, и bob, не может повесить группу bob на кампанию alice
(несовместимая цепочка → 404).

| Ручка | Поля | Владелец для проверки | Доп. условие цепочки |
|---|---|---|---|
| `POST/PATCH /campaigns` | `group_id`, `pixel_id`, `goal_id`, `scoring_profile_id` | владелец кампании (create: владелец рекламодателя) | `goal.pixel_id == campaign.pixel_id` (после PATCH) → иначе 422 |
| `POST /creatives` | `line_item_id` | владелец кампании | `line_item.campaign_id == body.campaign_id` |
| `POST/PATCH /creatives` | `counterparty_id`, `contract_id` | владелец кампании / креатива | — |
| `POST/PATCH /scoring/profiles` | `goal_id`, `pixel_id` | владелец профиля | — |
| `POST /ord/counterparties` | `advertiser_id` | scope пользователя (родитель) | — |
| `POST /ord/contracts` | `contractor_id`, `parent_contract_id` | владелец `client_id` | — |
| `POST /ord/creatives/{id}/register` | `contract_id` | владелец креатива | — |
| `PUT /line-items/{id}/creatives` | `creative_ids` | владелец позиции | `creative.campaign_id == line_item.campaign_id` (чужой id → 400) |

`goals` принадлежат владельцу пикселя (`goals.pixel_id → pixels.user_id`).

## 4. Billing

* `deposit`/`adjust`: рекламодатель ищется по `scope_for` (`FOR UPDATE`); finance/admin —
  любой рекламодатель платформы. `transactions.user_id` = кто провёл операцию (аудит).
* `transactions`/`summary`: фильтр по владельцу рекламодателя
  (`COALESCE(advertisers.user_id, transactions.user_id)`): проводка, сделанная finance
  клиенту, видна клиенту и не видна соседу.

## 5. Статус

* **B12a (этот документ, разделы 1–4 для CRUD/billing)** — реализовано:
  `campaigns`, `advertisers`, `groups`, `line_items`, `creatives`, `scoring` (профили,
  блок-листы, recompute), `ord_router`, `billing`.
* **B12b** — `stats.*` и `scoring/report` на `report_scope_for` (сейчас: платформенные роли
  в статистике видят только свой кабинет), viewer `advertiser_id` во всех GET
  (`/stats/domains`, счётчики summary), IDOR-тесты на все ссылочные поля.
