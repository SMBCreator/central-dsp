# Матрица RBAC по маршрутам

Сгенерировано `python backend/scripts/rbac_matrix.py --write` из объектов FastAPI;
`tests/test_rbac_matrix.py` падает, если файл устарел или нарушены инварианты
(read-only роли `viewer`/`analyst` не пишут; `finance` пишет только в `/billing`;
каждый мутирующий маршрут либо под `require_*`, либо явно обоснован).

Роли: superadmin, admin, manager, advertiser, analyst, finance, agency, client, viewer.

| Метод | Путь | Guard | Кто проходит |
|---|---|---|---|
| GET | `/advertisers` | session | любая роль (scope внутри хендлера) |
| **POST** | `/advertisers` | roles | admin, advertiser, agency, manager, superadmin |
| **DELETE** | `/advertisers/{advertiser_id}` | roles | admin, advertiser, agency, manager, superadmin |
| GET | `/advertisers/{advertiser_id}` | session | любая роль (scope внутри хендлера) |
| **PATCH** | `/advertisers/{advertiser_id}` | roles | admin, advertiser, agency, manager, superadmin |
| GET | `/audiences` | session | любая роль (scope внутри хендлера) |
| **POST** | `/audiences` | roles | admin, advertiser, agency, manager, superadmin |
| **DELETE** | `/audiences/{segment_id}` | roles | admin, advertiser, agency, manager, superadmin |
| **PATCH** | `/audiences/{segment_id}` | roles | admin, advertiser, agency, manager, superadmin |
| GET | `/audit/entities` | roles | admin, superadmin |
| GET | `/audit/log` | roles | admin, superadmin |
| GET | `/audit/log.csv` | roles | admin, superadmin |
| **POST** | `/auth/change-password` | session | любая роль (scope внутри хендлера) — self-service: только свой пароль |
| **POST** | `/auth/login` | none | публично |
| **POST** | `/auth/logout` | none | публично (гасит cookie) |
| GET | `/auth/me` | session | любая роль (scope внутри хендлера) |
| **PATCH** | `/auth/me` | session | любая роль (scope внутри хендлера) — self-service: только свой профиль |
| **POST** | `/auth/register` | none | публично (REGISTRATION_MODE) |
| **POST** | `/billing/adjust` | roles | admin, finance, superadmin |
| **POST** | `/billing/deposit` | roles | admin, finance, superadmin |
| GET | `/billing/summary` | roles+flag | admin, advertiser, agency, analyst, finance, manager, superadmin + client[can_view_billing] |
| GET | `/billing/transactions` | roles+flag | admin, advertiser, agency, analyst, finance, manager, superadmin + client[can_view_billing] |
| GET | `/campaigns` | session | любая роль (scope внутри хендлера) |
| **POST** | `/campaigns` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| **POST** | `/campaigns/ord-chain/preview` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| **DELETE** | `/campaigns/{campaign_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| GET | `/campaigns/{campaign_id}` | session | любая роль (scope внутри хендлера) |
| **PATCH** | `/campaigns/{campaign_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| GET | `/campaigns/{campaign_id}/blocks` | session | любая роль (scope внутри хендлера) |
| **DELETE** | `/campaigns/{campaign_id}/blocks/{block_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| GET | `/campaigns/{campaign_id}/diagnostics` | session | любая роль (scope внутри хендлера) |
| **POST** | `/campaigns/{campaign_id}/duplicate` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| GET | `/campaigns/{campaign_id}/ord-chain` | session | любая роль (scope внутри хендлера) |
| **POST** | `/campaigns/{campaign_id}/ord-chain/confirm` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| GET | `/campaigns/{campaign_id}/shading` | session | любая роль (scope внутри хендлера) |
| GET | `/creatives` | session | любая роль (scope внутри хендлера) |
| **POST** | `/creatives` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_manage_creatives] |
| **POST** | `/creatives/upload` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_manage_creatives] |
| **DELETE** | `/creatives/upload/{name}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_manage_creatives] |
| **DELETE** | `/creatives/{creative_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_manage_creatives] |
| **PATCH** | `/creatives/{creative_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_manage_creatives] |
| GET | `/cron/audience-sync` | none | Bearer CRON_SECRET |
| GET | `/cron/domain-quality` | none | Bearer CRON_SECRET |
| GET | `/cron/fx-refresh` | none | Bearer CRON_SECRET |
| GET | `/cron/ml-train` | none | Bearer CRON_SECRET |
| GET | `/cron/optimize` | none | Bearer CRON_SECRET |
| GET | `/cron/ord-export` | none | Bearer CRON_SECRET |
| GET | `/cron/ord-notifications` | none | Bearer CRON_SECRET |
| GET | `/cron/reconcile-reserves` | none | Bearer CRON_SECRET |
| GET | `/cron/reset-daily` | none | Bearer CRON_SECRET |
| GET | `/cron/retention` | none | Bearer CRON_SECRET |
| GET | `/cron/rules` | none | Bearer CRON_SECRET |
| GET | `/cron/shading` | none | Bearer CRON_SECRET |
| GET | `/cron/sweep-burl` | none | Bearer CRON_SECRET |
| GET | `/geo/search` | session | любая роль (scope внутри хендлера) |
| GET | `/groups` | session | любая роль (scope внутри хендлера) |
| **POST** | `/groups` | roles | admin, advertiser, agency, manager, superadmin |
| **DELETE** | `/groups/{group_id}` | roles | admin, advertiser, agency, manager, superadmin |
| **PATCH** | `/groups/{group_id}` | roles | admin, advertiser, agency, manager, superadmin |
| GET | `/health` | none | публично |
| GET | `/line-items` | session | любая роль (scope внутри хендлера) |
| **POST** | `/line-items` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| **DELETE** | `/line-items/{line_item_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| **PATCH** | `/line-items/{line_item_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| **PUT** | `/line-items/{line_item_id}/creatives` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| GET | `/metrics` | none | публично (сеть Prometheus) |
| GET | `/moderation/queue` | roles | admin, superadmin |
| **POST** | `/moderation/{creative_id}/approve` | roles | admin, superadmin |
| **POST** | `/moderation/{creative_id}/reject` | roles | admin, superadmin |
| GET | `/notifications` | session | любая роль (scope внутри хендлера) |
| **POST** | `/notifications/read-all` | session | любая роль (scope внутри хендлера) — self-service: только свои уведомления |
| **POST** | `/notifications/{notification_id}/read` | session | любая роль (scope внутри хендлера) — self-service: WHERE user_id = me |
| GET | `/ord/contracts` | session | любая роль (scope внутри хендлера) |
| **POST** | `/ord/contracts` | roles | admin, manager, superadmin |
| **POST** | `/ord/contracts/{contract_id}/register` | roles | admin, manager, superadmin |
| GET | `/ord/counterparties` | session | любая роль (scope внутри хендлера) |
| **POST** | `/ord/counterparties` | roles | admin, manager, superadmin |
| **POST** | `/ord/counterparties/{cp_id}/register` | roles | admin, manager, superadmin |
| GET | `/ord/creatives` | session | любая роль (scope внутри хендлера) |
| **POST** | `/ord/creatives/{creative_id}/register` | roles | admin, manager, superadmin |
| GET | `/ord/creatives/{creative_id}/versions` | session | любая роль (scope внутри хендлера) |
| GET | `/ord/exports` | session | любая роль (scope внутри хендлера) |
| GET | `/ord/exports/{log_id}/rows` | session | любая роль (scope внутри хендлера) |
| GET | `/ord/invoices` | session | любая роль (scope внутри хендлера) |
| **POST** | `/ord/invoices` | roles | admin, manager, superadmin |
| GET | `/ord/invoices/preview` | session | любая роль (scope внутри хендлера) |
| **DELETE** | `/ord/invoices/{invoice_id}` | roles | admin, manager, superadmin |
| GET | `/ord/invoices/{invoice_id}` | session | любая роль (scope внутри хендлера) |
| **PUT** | `/ord/invoices/{invoice_id}` | roles | admin, manager, superadmin |
| **POST** | `/ord/invoices/{invoice_id}/submit` | roles | admin, manager, superadmin |
| GET | `/ord/notifications` | session | любая роль (scope внутри хендлера) |
| **POST** | `/ord/notifications/sync` | roles | admin, manager, superadmin |
| GET | `/ord/platform` | session | любая роль (scope внутри хендлера) |
| **POST** | `/ord/platform/register` | roles | admin, manager, superadmin |
| **POST** | `/pixel/collect` | none | публично (пиксель), rate-limit |
| GET | `/pixel/script/{pixel_id}.js` | none | публично (пиксель) |
| GET | `/pixels` | session | любая роль (scope внутри хендлера) |
| **POST** | `/pixels` | roles | admin, advertiser, agency, manager, superadmin |
| **DELETE** | `/pixels/{pixel_id}` | roles | admin, advertiser, agency, manager, superadmin |
| GET | `/pixels/{pixel_id}/goals` | session | любая роль (scope внутри хендлера) |
| **POST** | `/pixels/{pixel_id}/goals` | roles | admin, advertiser, agency, manager, superadmin |
| **DELETE** | `/pixels/{pixel_id}/goals/{goal_id}` | roles | admin, advertiser, agency, manager, superadmin |
| GET | `/pixels/{pixel_id}/stats` | session | любая роль (scope внутри хендлера) |
| GET | `/rtb-feed/campaigns` | none | Bearer RTB_API_TOKEN |
| GET | `/rtb-feed/partners` | none | Bearer RTB_API_TOKEN |
| **POST** | `/rtb/bid/{endpoint_key}` | none | endpoint_key / event-токен (HMAC) |
| GET | `/rtb/burl` | none | endpoint_key / event-токен (HMAC) |
| GET | `/rtb/click` | none | endpoint_key / event-токен (HMAC) |
| GET | `/rtb/imp` | none | endpoint_key / event-токен (HMAC) |
| GET | `/rtb/vevent` | none | endpoint_key / event-токен (HMAC) |
| GET | `/rtb/view` | none | endpoint_key / event-токен (HMAC) |
| GET | `/rtb/win` | none | endpoint_key / event-токен (HMAC) |
| GET | `/rules` | session | любая роль (scope внутри хендлера) |
| **POST** | `/rules` | roles | admin, manager, superadmin |
| **POST** | `/rules/evaluate` | roles | admin, manager, superadmin |
| GET | `/rules/templates` | session | любая роль (scope внутри хендлера) |
| **DELETE** | `/rules/{rule_id}` | roles | admin, manager, superadmin |
| **PATCH** | `/rules/{rule_id}` | roles | admin, manager, superadmin |
| GET | `/scoring/blocks` | session | любая роль (scope внутри хендлера) |
| **POST** | `/scoring/blocks` | roles | admin, manager, superadmin |
| **DELETE** | `/scoring/blocks/{campaign_id}/{domain}` | roles | admin, manager, superadmin |
| GET | `/scoring/profiles` | session | любая роль (scope внутри хендлера) |
| **POST** | `/scoring/profiles` | roles | admin, manager, superadmin |
| **DELETE** | `/scoring/profiles/{profile_id}` | roles | admin, manager, superadmin |
| **PATCH** | `/scoring/profiles/{profile_id}` | roles | admin, manager, superadmin |
| **POST** | `/scoring/recompute` | roles | admin, manager, superadmin |
| GET | `/scoring/report` | session | любая роль (scope внутри хендлера) |
| GET | `/scoring/report.csv` | session | любая роль (scope внутри хендлера) |
| GET | `/settings` | session | любая роль (scope внутри хендлера) |
| **PATCH** | `/settings` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| GET | `/settings/domain-lists` | session | любая роль (scope внутри хендлера) |
| **POST** | `/settings/domain-lists` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| **DELETE** | `/settings/domain-lists/{item_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_edit_campaigns] |
| GET | `/settings/fx` | session | любая роль (scope внутри хендлера) |
| GET | `/settings/ml/status` | roles | admin, superadmin |
| **DELETE** | `/settings/ord` | roles | superadmin |
| GET | `/settings/ord` | roles | superadmin |
| **PUT** | `/settings/ord` | roles | superadmin |
| **POST** | `/settings/ord/test` | roles | superadmin |
| GET | `/settings/public` | session | любая роль (scope внутри хендлера) |
| **DELETE** | `/settings/throttle/subnets` | roles | admin, superadmin |
| GET | `/settings/throttle/subnets` | roles | admin, superadmin |
| **POST** | `/settings/throttle/subnets` | roles | admin, superadmin |
| GET | `/share/guests` | session | любая роль (scope внутри хендлера) |
| **POST** | `/share/guests` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_share_stats] |
| **DELETE** | `/share/guests/{guest_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_share_stats] |
| GET | `/share/links` | session | любая роль (scope внутри хендлера) |
| **POST** | `/share/links` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_share_stats] |
| **DELETE** | `/share/links/{link_id}` | roles+flag | admin, advertiser, agency, manager, superadmin + client[can_share_stats] |
| GET | `/share/my-scope` | session | любая роль (scope внутри хендлера) |
| GET | `/share/public/{token}` | none | share-токен в URL |
| **POST** | `/simulator/run` | session | любая роль (scope внутри хендлера) — владелец SSP-партнёра (WHERE user_id = me); идёт через Python-RTB без test=1 — события не помечены is_test |
| GET | `/stats/breakdown` | session | любая роль (scope внутри хендлера) |
| GET | `/stats/domain-quality` | session | любая роль (scope внутри хендлера) |
| GET | `/stats/domains` | session | любая роль (scope внутри хендлера) |
| **POST** | `/stats/report` | session | любая роль (scope внутри хендлера) — чтение через POST (тело = фильтры), scope visible_user_ids |
| GET | `/stats/summary` | session | любая роль (scope внутри хендлера) |
| GET | `/stats/timeseries` | session | любая роль (scope внутри хендлера) |
| GET | `/supply-partners` | session | любая роль (scope внутри хендлера) |
| **POST** | `/supply-partners` | roles | admin, superadmin |
| **DELETE** | `/supply-partners/{partner_id}` | roles | admin, superadmin |
| **PATCH** | `/supply-partners/{partner_id}` | roles | admin, superadmin |
| **POST** | `/supply-partners/{partner_id}/check` | roles | admin, superadmin |
| GET | `/supply-partners/{partner_id}/stats` | session | любая роль (scope внутри хендлера) |
| **DELETE** | `/supply-partners/{partner_id}/token` | roles | admin, superadmin |
| **POST** | `/supply-partners/{partner_id}/token` | roles | admin, superadmin |
| GET | `/users` | session | любая роль (scope внутри хендлера) |
| **POST** | `/users` | roles | admin, agency, superadmin |
| GET | `/users/me/markup` | session | любая роль (scope внутри хендлера) |
| **PATCH** | `/users/{user_id}` | roles | admin, agency, superadmin |

Чтение (`GET`) под `session` изолируется per-query через `visible_user_ids`/`WHERE user_id` —
покрыто `tests/test_idor_isolation_db.py`.
