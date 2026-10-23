# ID sync (cookie matching)

План: `docs/audit/AGENT_TASK_predeploy_idsync_geo_ord.md`, раздел A.3–A.9.
Статус: код готов, **по умолчанию выключен** (`ID_SYNC_ENABLED=0`).
Включение в production — только после юридического согласования (A.0).

## Зачем

Bid request SSP приходит с ID пользователя в пространстве SSP (`user.id`
или `user.eids`). Без соответствия этот ID не совпадает с нашими
аудиториями и частотой. Sync связывает чужой ID с нашим `dsp_uid`
(cookie первой стороны на домене sync) — и биддер находит пользователя
в любом подключённом SSP.

## Поток

```
SSP-пиксель ──► GET /sync/{endpoint_key}/in?ssp_uid=…&gdpr=…&gdpr_consent=…
                 ├─ согласие проверено (requires_consent)
                 ├─ dsp_uid: из cookie или новый (uuid4 hex)
                 ├─ id_sync_map(partner, sha256(salt||ssp_uid)) → dsp_uid
                 ├─ Redis idsync:{partner_id}:{hash} → dsp_uid (TTL)
                 └─ 302 на callback SSP с {dsp_uid} либо 1×1 gif

DSP-ссылка  ──► GET /sync/{endpoint_key}/out?ts=…&sig=…   (HMAC, одноразовая, 10 мин)
                 └─ 302 на sync_callback_url_template с {dsp_uid}

Bid request ──► bidder-go resolveIdentity:
                 buyeruid → device.ifa (не нулевой) → user.id/eids через idsync:* → нет ID
```

Чужой ID нигде не хранится в открытом виде: в PG и Redis — только
`sha256(salt || ssp_uid)`, соль своя у каждого партнёра
(`supply_partners.id_sync_salt`), в логах — усечённый хэш.

## Настройка партнёра

Кабинет → Supply → карточка SSP → **ID sync**. Поля (`supply_partners.identity`):

| Поле | Смысл |
| --- | --- |
| `sync_enabled` | sync для этого SSP (действует только при глобальном флаге) |
| `id_source` | `user_id` или `eids:<source>`; `buyeruid`/`ifa` — sync не нужен |
| `sync_in_param` | имя query-параметра с ID в пикселе `/in` (по умолчанию `ssp_uid`) |
| `sync_callback_url_template` | https-URL SSP с макросом `{dsp_uid}` |
| `sync_redirect_hosts` | allowlist доменов редиректа (защита от open redirect) |
| `ttl_days` | срок жизни соответствия, 1–395 |
| `requires_consent` | без строки согласия соответствие не создаётся |

Там же — готовые URL для анкеты SSP (`GET /supply-partners/{id}/sync-urls`)
и счётчик активных соответствий.

## Переменные окружения

| Переменная | Где | Назначение |
| --- | --- | --- |
| `ID_SYNC_ENABLED` | backend, bidder-go | глобальный флаг; off = прежнее поведение |
| `ID_SYNC_SECRET` | backend | HMAC подписи `/out`; без него `/out` отключён |
| `ID_SYNC_BASE_URL` | backend | база URL пикселей (иначе `PUBLIC_BASE_URL`) |
| `ID_SYNC_COOKIE_DAYS` | backend | срок cookie `dsp_uid` |
| `ID_SYNC_IP_RPM`, `ID_SYNC_PARTNER_RPM` | backend | rate limit `/sync/*` |

Caddy проксирует `/sync/*` на backend; рекомендуется вынести на поддомен
`sync.<DOMAIN>`, чтобы cookie не пересекалась с кабинетом.

## Права пользователя

* `GET /sync/optout` — удаляет все соответствия текущего `dsp_uid` и ставит
  cookie-отказ; последующие `/in` соответствий не создают.
* `POST /sync/delete` — то же для запроса на удаление данных.

## Джобы

| Джоба | Расписание | Что делает |
| --- | --- | --- |
| `id_sync_purge` | ежедневно 00:40 | удаляет просроченные строки `id_sync_map` |
| `rebuild_idsync` | ежечасно :20 | досевает `idsync:*` в Redis из PG после потери Redis |

При выключенном флаге обе — no-op.

## Метрики и алерты

* backend: `id_sync_requests_total{partner,status}`, `id_sync_mappings_created_total`,
  `id_sync_redirect_rejected_total`, `id_sync_consent_denied_total`.
* bidder-go: `bidder_identity_source_total{partner,source}`
  (`buyeruid|ifa|sync|none`), `bidder_audience_match_total`,
  `bidder_idsync_lookup_errors_total`.
* Алерт `IdentifiedShareDrop` — доля опознанных запросов у SSP упала
  относительно суток назад (runbook: `docs/runbooks/rtb_alerts.md`).

Lookup в Redis не блокирует аукцион: ошибка/таймаут → запрос идёт как
«без ID», ставка не теряется.

## Включение (чек-лист A.9)

1. Юр. согласование: политика конфиденциальности, тексты согласия, договор с SSP.
2. `ID_SYNC_SECRET` сгенерирован, задан в `.env`.
3. Миграция `058_id_sync.sql` применена (`migrate.py`).
4. `ID_SYNC_ENABLED=1` у backend **и** bidder-go, рестарт.
5. Один SSP: заполнить ID sync в кабинете, отправить URL пикселя, дождаться
   роста «Активных соответствий» и `bidder_identity_source_total{source="sync"}`.
6. Следить за `IdentifiedShareDrop` и долей `none` сутки, затем следующие SSP.

## Откат

1. `ID_SYNC_ENABLED=0` у backend и bidder-go, рестарт — биддер сразу перестаёт
   читать `idsync:*`, `/sync/*` отвечает 404. Данные остаются.
2. Полное удаление: `backend/migrations/down/058_id_sync.down.sql`
   (дропает `id_sync_map` и колонки) и `redis-cli --scan --pattern 'idsync:*' | xargs redis-cli del`.
