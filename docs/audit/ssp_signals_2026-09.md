# Сигналы от SSP: что DSP получает, что из этого есть у нас, чего нет

Дата: 2026-09-04. Ветка `feat/avito-ui-kktu`. Источник фактов о нашем коде — файлы и строки в колонке «У нас».
Боевой биддер — только `bidder-go/`; Python `rtb.py` упоминается как паритет.

## 1. Откуда у DSP вообще берутся данные

DSP видит инвентарь **только** через OpenRTB BidRequest от SSP и через уведомления после аукциона.
Своих данных о пользователе у DSP нет, кроме тех, что он сам собрал (пиксель, ретаргетинг, история своих показов/кликов).
Поэтому качество таргетинга и оптимизации целиком определяется тем, (а) какие поля запроса мы **парсим**,
(б) по каким **таргетируемся**, (в) какие **записываем в события** для отчётов/скоринга/ML, (г) какие
пост-аукционные сигналы (win/loss/цена/видимость) мы **получаем и учитываем**.

## 2. Карта сигналов OpenRTB 2.5/2.6 и наше покрытие

Обозначения: П — парсим (`bidder-go/internal/rtb/models.go`), Т — есть таргетинг (`targeting.go`),
Л — логируем в `bid_events` (`handler.go:426-431` → `bidmeta`; `backend/migrations/000_baseline.sql:139`), «—» нет.

### 2.1 Impression (`imp`)

| Поле | Зачем DSP | У нас | Вывод |
|---|---|---|---|
| `imp.bidfloor`, `bidfloorcur` | не бидить ниже пола, шейдинг | П, учитывается `handler.go:252-254` | ок |
| `imp.tagid` (placement id) | **ключевой разрез оптимизации**: win-rate/CTR по площадке внутри домена | — | **добавить П+Л**, разрез в отчётах |
| `banner.w/h/format/pos` | размер/позиция | П, Т (`banner_positions`) | ок |
| `banner.btype`, `battr` | запрет типов/атрибутов креатива издателем; иначе SSP отбрасывает бид, портится quality-score | — | **добавить П + фильтр** |
| `video.mimes/min/maxduration/skip` | совместимость видео | П, Т | ок |
| `video.protocols`, `plcmt/placement`, `startdelay`, `playbackmethod`, `linearity` | VAST-версия, in-stream vs out-stream, pre/mid-roll, автоплей — без них видео-бид либо отклоняется, либо показывается не там | — | **добавить П + Т** (`video_placements`, `video_startdelay`) |
| `imp.instl`, `rwdd`, `secure` | interstitial / rewarded / https | П, Т | ок |
| `imp.pmp.deals[]` | PMP-сделки, цена сделки | П, Т (`deal_ids`), `bid.dealid` | ок |
| `imp.metric[type=viewability]` | **прогноз видимости от SSP** — стандартный сигнал для порога «не бидить, если viewability < X» | — | **добавить П + Т** (`min_viewability`) |
| `imp.exp` | срок жизни бида | — | не критично |
| `imp.ext.gpid` | глобальный placement id (кросс-SSP) | — | вместе с `tagid` |

### 2.2 Инвентарь: `site` / `app`

| Поле | Зачем DSP | У нас | Вывод |
|---|---|---|---|
| `site.domain`, `page` | allow/block-листы, скоринг доменов | П, Т, Л | ок |
| `site.ref`, `search` | контекст перехода | — | не критично |
| `site.cat`, `app.cat` | IAB-категории | П, Т | ок |
| `site.pagecat`, `sectioncat` | категория **страницы** (точнее сайта) | — | добавить П, учитывать в `iab_*` |
| `publisher.id/name/domain` | allow/block издателя | П, Т | ок; **Л нет** — добавить `publisher_id` в события |
| `content.*` (`language`, `genre`, `livestream`, `contentrating`, `keywords`, `title`) | контекст, CTV | П частично (`keywords`, `title`) | добавить `content.language` в языковой таргетинг |
| `app.bundle`, `storeurl`, `ver`, `paid` | allow/block приложений, проверка на модерации | П (`bundle`), Т | ок; `storeurl` — для модерации, позже |

### 2.3 Устройство и гео: `device`

| Поле | Зачем DSP | У нас | Вывод |
|---|---|---|---|
| `ua`, `sua` | ОС/браузер fallback | П (`ua`), браузер только в Python `_device_browser` `rtb.py:57` | **Go пишет `browser: ""`** `handler.go:431` — портировать |
| `devicetype`, `os`, `osv` | тип/ОС/версия | П, Т, Л | ок |
| `make`, `model` | таргетинг на бренд/модель (iPhone-only, Samsung и т.п.), антифрод (пустые make на «настольных» UA) | — | **добавить П + Т + Л** |
| `geo.country/region/city` | гео | П, Т; Л только country | добавить `region/city` в Л |
| `geo.lat/lon`, `geo.type` | гиперлокальный радиус; `type=1` GPS точнее IP | П (`lat/lon`), Т — | радиус — P2 |
| `geo.zip`, `metro`, `utcoffset` | почтовые индексы; **локальное время пользователя** для дневного парттинга | — | `utcoffset` — P2 (сейчас парттинг только UTC) |
| `ip`, `ipv6` | IP-диапазоны, антифрод, **GeoIP когда `geo` не передан** | П, Т (`ip_ranges`) | **GeoIP нет ни в Go, ни в Python** — если SSP не прислал `geo`, гео-таргет даёт no-bid. P1 (GeoLite2 в Go) |
| `connectiontype`, `carrier`, `mccmnc` | wifi/сотовая, оператор | П, Т; Л — | добавить в Л |
| `language` | язык | П, Т | ок |
| `ifa`, `lmt`, `dnt` | частотный кап, ретаргетинг, приватность | П, Т (`skip_dnt`), кап по `ifa` | ок |
| `h/w/ppi/pxratio`, `js` | подбор размеров, антифрод | — | не критично |

### 2.4 Пользователь и регуляторика: `user`, `regs`, `source`

| Поле | Зачем DSP | У нас | Вывод |
|---|---|---|---|
| `user.id`, `buyeruid` | cookie-sync id → ретаргетинг | П, используется для аудиторий | ок |
| `user.yob`, `gender` | демография (редко заполняется SSP) | П, Т | ок |
| `user.data[].segment[]` | **сегменты данных от SSP/DMP** (интересы, соц-дем) — второй по важности источник аудиторного таргетинга после своих пикселей | — | **добавить П + Т** (`ssp_segments_include/exclude`) + Л |
| `user.eids` | внешние id | П | ок |
| `user.consent`, `regs.gdpr/coppa` | приватность | П, Т | ок |
| `regs.us_privacy`, `gpp` | США — не наш рынок | — | не нужно |
| `source.schain` | **цепочка поставки**: только прямые продавцы (`hp=1`, 1 узел) — антифрод, отсечение перепродаж | — | **добавить П + Т** (`schain_direct_only`, `schain_max_hops`) + Л (`schain_hops`) |
| `source.ext.omidpn/omidpv` | поддержка Open Measurement (замер видимости) | — | вместе с viewability, P2 |
| top-level `bcat`, `badv`, `bapp` | издатель блокирует категории/рекламодателей/приложения | `badv` — да; **`bcat` не парсится** | **добавить `bcat`** против категории креатива/кампании |
| `test` | тестовый запрос — не списывать деньги | — | **добавить**: в статусе SSP `testing` считать биды, но не расход |
| `tmax`, `at`, `cur`, `wseat` | тайминг, тип аукциона, валюта | П | ок |

### 2.5 После аукциона (обратная связь — то, на чём строится оптимизация)

| Сигнал | Зачем DSP | У нас | Вывод |
|---|---|---|---|
| `nurl` (win + `${AUCTION_PRICE}`) | факт выигрыша и клиринговая цена | есть `handler.go:324` → backend `/api/rtb/win` | ок |
| `burl` (billing notice) | факт биллинга (у ряда SSP отдельно от win) | — | добавить, обрабатывать как win-подтверждение |
| `lurl` + `${AUCTION_LOSS}` (код причины), `${AUCTION_MIN_TO_WIN}` (2.6) | **почему проиграли и сколько не хватило** — главный сигнал для bid shading и для диагностики «SSP режет наши биды» | `Loss` handler есть (`handler.go:454`), но **`lurl` в ответ не ставится** — SSP нас не вызывает; причина и min-to-win не пишутся | **добавить `lurl` + запись причины/min-to-win в события**, разрез в отчётах |
| imp/click трекеры | показ/клик | есть | ок |
| viewability (OMID/вендор) | видимость показа | есть `viewable_imps` в `stats_hourly` | ок |
| **события `bid`** | без них нет win-rate, нет разрезов бидов, нет данных для pCTR/шейдинга | пишет только Python `rtb.py:1002`; **Go не пишет ничего** | **критично: Go → Redis Stream → backend consumer → `bid_events`/`stats_hourly`** |
| отчёты SSP (offline: bid landscape, win-rate по площадкам) | сверка | — | ручной процесс, вне кода |

## 3. Что уже используется в оптимизации внутри платформы

- Скоринг доменов (`domain_quality_recompute`, профили на `/scoring`) — по `domain`, нет `tagid`/`publisher`/SSP-разреза.
- Bid shading (`bidder-go/internal/shading`, `/cron/shading`) — по `stats_hourly`; без loss-сигнала шейдинг работает «вслепую» (только по win/no-win).
- pCTR (`pctr` в событиях, `/cron/ml-train`) — обучается на событиях Python; с Go событий не будет.
- Отчёты: разрезы `campaign/creative/supply_partner/country/device/os/browser/ad_type/domain` (`stats.py:339-366`). По SSP разрез **есть** (`supply_partner`), но данные бидов от Go — нет (п.2.5).

## 4. Приоритеты доработок (в порядке выполнения)

P0 — без этого продукт на Go-биддере не работает как DSP:
1. События `bid` из Go → `bid_events`/`stats_hourly` (Redis Stream + consumer в backend). Расширить payload: `browser`, `bundle`, `publisher_id`, `tagid`, `pos`, `deal_id`, `bidfloor`, `region`, `city`, `connection_type`, `carrier`, `make`, `model`, `schain_hops`, `viewability`.
2. Партнёр в статусе `testing`: фид и Go принимают `active|testing`; при `testing` или `test=1` — биды считаем, деньги не списываем, события помечаем `is_test`.
3. Проверка подключения SSP — через боевой Go по HTTP, а не через Python.
4. `lurl` в ответе + запись `loss_reason`/`min_to_win`.
5. Rate-limit логина по логину и IP.

P1 — качество торгов:
6. `bcat`/`battr`/`btype` фильтры; `video.protocols/plcmt/startdelay` парсинг и таргетинг.
7. Новые ключи таргетинга: `device_makes/models`, `min_viewability`, `schain_direct_only`, `ssp_segments`, `pagecat` в IAB.
8. GeoIP-fallback по IP в Go (GeoLite2), когда `device.geo` пуст.
9. Разрезы отчётов и скоринга по `tagid`, `publisher_id`, `loss_reason`; карточка SSP на `/supply` с win-rate/латентностью/причинами потерь за 24 ч.

P2: радиус по lat/lon, локальное время (`utcoffset`), OMID, `storeurl` на модерации.

Правило для любого нового ключа: UI (`targeting-editor.tsx`) → фид (`bidder_feed.py`) → Go (`feedTargetingToMatch`/`feedImpTargeting` + `targeting.Match`) → тест Go → событие (если нужен разрез).
