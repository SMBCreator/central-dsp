# GEO v2: справочник мест и таргетинг по id

Старый гео-таргетинг (`geo_countries` / `geo_regions` / `geo_cities`) сравнивает
строки из запроса SSP с названиями. GEO v2 сначала сопоставляет `device.geo`
запроса со справочником мест, а затем проверяет выбранные места по id. Так
работают синонимы, коды ISO 3166-2, иерархия «город → регион → страна» и
алиасы под каждую SSP.

Старые ключи работают как раньше. Если у группы заданы и старые, и v2-ключи,
проверяются оба условия (AND).

## Данные

Миграция `backend/migrations/060_geo_places.sql` (откат — `migrations/down/060_geo_places.down.sql`):

| Таблица | Назначение |
| --- | --- |
| `geo_dataset_versions` | версии справочника; активна одна (`is_active`) |
| `geo_places` | места: `id` = geonameid, `type` (country / region / city / town), `parent_id`, `region_code` (ISO 3166-2), `aliases` (нормализованные синонимы), координаты, население |
| `geo_ssp_alias` | значения конкретной SSP (`partner_id`, `kind` = region / city, `value_norm`) → место |

Импорт: `ops/geo_import.py` (GeoNames CC BY 4.0 + коды ISO 3166-2 из Wikidata CC0).

```bash
python ops/geo_import.py --dsn "$DATABASE_URL"                 # скачать и загрузить
python ops/geo_import.py --dsn ... --data-dir /var/cache/geo   # с кэшем файлов
python ops/geo_import.py --data-dir ... --offline --dry-run    # только статистика
```

Импорт идемпотентный. id мест стабильны (geonameid), поэтому `geo_place_ids`
в таргетинге остаются валидными после повторного импорта. Места, пропавшие
из источника, не удаляются.

## Таргетинг

| Ключ | Тип | Смысл |
| --- | --- | --- |
| `geo_place_ids` | `int[]` | включить места; место включает всех своих потомков |
| `geo_exclude_place_ids` | `int[]` | исключить места; исключение важнее включения |
| `geo_unknown` | `"strict"` (по умолчанию) / `"lenient"` | что делать, если гео запроса не распознано |

Не больше `MAX_GEO_PLACES` (500) мест на группу. Одно место нельзя одновременно
включить и исключить (`backend/targeting_contract.py`).

`lenient` пропускает запрос, если:

* гео не распознано вовсе (нет `device.geo` или ни одно значение не найдено);
* гео распознано не до конца, но найденное место содержит выбранное.
  Пример: SSP прислала только страну RU, а таргет — «Краснодарский край».
  Если известен город, работает обычное правило: другой город не проходит.

Исключения проверяются и в `lenient`.

## Сопоставление запроса

`device.geo` → цепочка мест (`geo_v2.GeoIndex.resolve` в Python,
`internal/geo.Index.Resolve` в Go; кейсы общие — `bidder-go/internal/geo/testdata/fixtures.json`):

1. Страна: alpha-2 или alpha-3 (OpenRTB 2.x передаёт alpha-3).
2. Регион: алиас SSP → код ISO 3166-2 (`RU-KDA` или `KDA`) → синоним.
   Если страны нет, её подставляет найденный регион.
3. Город: алиас SSP → синоним в найденном регионе → синоним в стране
   (самый населённый). Без страны город находится только по алиасу SSP.
4. Если включён `GEO_LATLON_ENABLED`, город не найден и есть `lat`/`lon` —
   берётся ближайший город в радиусе `GEO_LATLON_RADIUS_KM`.

Уровень результата — `city`, `region`, `country` или `unknown`. Нераспознанные
значения `region`/`city` копятся по партнёрам: из них пополняют `geo_ssp_alias`.

## Флаги

| Переменная | Где | По умолчанию | Смысл |
| --- | --- | --- | --- |
| `GEO_V2_ENABLED` | backend | выкл. | v2-ключи работают; фид отдаёт `geo_version` |
| `GEO_LATLON_ENABLED` | backend и bidder | выкл. | ближайший город по координатам |
| `GEO_LATLON_RADIUS_KM` | backend и bidder | 25 | радиус поиска по координатам |

Когда `GEO_V2_ENABLED` выключен, группы с v2-ключами **не показываются**
(fail-closed). Иначе кампания на один регион крутилась бы везде. UI в этом
случае показывает предупреждение.

## Go-биддер

* Фид кампаний (`/rtb-feed/campaigns`) содержит `geo_version`. Справочник
  (`GET /rtb-feed/geo`) Go перезагружает только при смене версии. Если загрузка
  не удалась, остаётся предыдущий индекс.
* Гео запроса сопоставляется один раз на запрос и только тогда, когда хотя бы
  у одной кампании есть v2-гео. Иначе hot path не меняется.
* Таргетинг группы превращается в множества при рефреше фида
  (`geo.NewSets` + `Bind`). Проверка кандидата — O(длина цепочки), без аллокаций.
* Метрика: `bidder_geo_resolve_total{level="city|region|country|unknown"}`.
* `GET /internal/geo/unmatched?limit=N` (заголовок `X-CentralDSP-Internal-Check`
  с `RTB_API_TOKEN`) — самые частые нераспознанные значения по партнёрам.

## API кабинета

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/geo/config` | флаг, активная версия, лимит мест |
| GET | `/geo/places/search?q=` | автокомплит |
| GET | `/geo/places/{id}/children` | регионы страны, города региона |
| GET | `/geo/places?ids=` | подписи выбранных мест |
| POST | `/geo/places/resolve` | список названий → места (found / ambiguous / not_found) |
| GET / PUT | `/supply-partners/{id}/geo-aliases` | алиасы SSP (только admin) |

## Включение в production

1. Применить миграцию 060 и выполнить `ops/geo_import.py`.
2. Выставить `GEO_V2_ENABLED=1` на backend. Go подхватит справочник при следующем рефреше фида.
3. Смотреть на `bidder_geo_resolve_total` и `/internal/geo/unmatched`, добавлять алиасы для частых нераспознанных значений.
