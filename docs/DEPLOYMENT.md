# Развёртывание CentralDSP на сервере

Пошаговый гайд: от голого VPS до работающего стека (Postgres + Redis + Backend API +
Go-биддер + Celery + Frontend) за реверс-прокси с HTTPS.

---

## 1. Минимальные требования (тестовый контур)

| Ресурс | Минимум (тест) | Рекомендация (пилот с 1 SSP, ~500 QPS) |
|---|---|---|
| CPU | 2 vCPU | 4–8 vCPU |
| RAM | 4 GB | 8–16 GB |
| Диск | 40 GB SSD | 100+ GB NVMe |
| ОС | Ubuntu 22.04/24.04 LTS | то же |
| Сеть | публичный IPv4, домен | то же + низкий RTT до SSP |

Для тестов всё умещается на одном VPS. Для боевого трафика биддер выносится
на отдельную машину рядом с дата-центром SSP.

## 2. Подготовка сервера

```bash
# Обновление и базовые пакеты
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ufw

# Docker + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Firewall: только SSH, HTTP, HTTPS
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 3. Клонирование и конфигурация

```bash
git clone https://github.com/SMBCreator/central-dsp.git
cd central-dsp
cp .env.example .env 2>/dev/null || touch .env
```

Заполните `.env` (все секреты генерируйте через `openssl rand -hex 32`):

```env
# --- База данных ---
POSTGRES_DB=centraldsp
POSTGRES_USER=centraldsp
POSTGRES_PASSWORD=<openssl rand -hex 16>
DATABASE_URL=postgresql://centraldsp:<тот же пароль>@postgres:5432/centraldsp

# --- Redis ---
REDIS_URL=redis://redis:6379/0

# --- Секреты (ОБЯЗАТЕЛЬНО уникальные, не из примеров!) ---
SESSION_SECRET=<openssl rand -hex 32>
RTB_API_TOKEN=<openssl rand -hex 32>
EVENT_TOKEN_SECRET=<openssl rand -hex 32>

# --- Прод-флаги ---
APP_ENV=production
PUBLIC_BASE_URL=https://ваш-домен.ру

# --- Celery monitoring ---
FLOWER_PASSWORD=<пароль для Flower UI>

# --- ОРД Озон (можно позже задать через UI настроек) ---
# ORD_OZON_API_KEY=<ключ из кабинета ОРД Озон>
```

## 4. Запуск стека

```bash
# Собрать и поднять всё (Go-биддер включается профилем)
docker compose --profile bidder-go up -d --build postgres redis backend bidder-go worker beat frontend

# Проверить статусы
docker compose ps
docker compose logs backend --tail 20
docker compose logs bidder-go --tail 20
```

Миграции БД применяются автоматически при старте backend (`python migrate.py`).

Проверка изнутри сервера:

```bash
curl -s http://localhost:8000/health        # backend -> {"status":"ok"}
curl -s http://localhost:8090/healthz       # go-биддер -> feed fresh, redis ok
curl -s http://localhost:3000 -o /dev/null -w "%{http_code}\n"  # frontend -> 200
```

## 5. Реверс-прокси с HTTPS (Caddy — проще всего)

```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile > /dev/null << 'EOF'
ваш-домен.ру {
    # API -> FastAPI backend
    handle /api/* {
        uri strip_prefix /api
        reverse_proxy localhost:8000
    }
    # RTB endpoint для SSP -> Go-биддер
    handle /rtb/* {
        reverse_proxy localhost:8090
    }
    # Всё остальное -> Next.js UI
    handle {
        reverse_proxy localhost:3000
    }
}
EOF
sudo systemctl reload caddy
```

Caddy сам получит и будет продлевать TLS-сертификат Let's Encrypt.
SSP-партнёру отдаёте URL вида: `https://ваш-домен.ру/rtb/bid/<endpoint_key>`.

## 6. Первичная настройка через UI

1. Откройте `https://ваш-домен.ру` — вход супер-админа
   (создаётся сидом; сразу смените пароль).
2. **Настройки -> ОРД Озон**: вставьте API-ключ из кабинета ОРД Озон
   (Настройки -> API). Выберите «Песочница» для проверки, потом «Прод».
   Нажмите «Проверить связь».
3. **Финансы**: пополните баланс рекламодателя (кнопка «Пополнить» —
   ручная проводка супер-админа, п. биллинга ниже).
4. **SSP-партнёры**: создайте партнёра, скопируйте endpoint key, отдайте
   SSP URL эндпоинта.
5. Создайте рекламодателя -> кампанию -> креатив (загрузкой файла) ->
   line item. Креатив уйдёт на регистрацию в ОРД, erid появится в карточке.

## 7. Как работает биллинг (для супер-админа)

- **Деньги рекламодателей**: собираете по договору на счёт юрлица, затем
  вручную пополняете баланс кабинета («Финансы» -> «Пополнить»). Система
  списывает с баланса за каждый выигранный показ (по цене клиринга + маржа).
- **Долг перед SSP**: копится автоматически (отчёт «Финансы -> SSP»).
  Оплачиваете по договору с SSP (обычно постоплата раз в месяц по их отчёту).
- **Ваша маржа** = списано с рекламодателей - выплачено SSP.
- Расхождения (дискрепанс) с отчётом SSP — нормальны до 5–10%; сверяйте
  ежемесячно.

## 8. Обновление версии

```bash
cd central-dsp
git pull
docker compose --profile bidder-go up -d --build
docker compose logs backend --tail 10   # миграции применились?
```

## 9. Бэкапы (обязательно с первого дня)

```bash
# Ежедневный дамп БД (добавьте в crontab)
docker compose exec -T postgres pg_dump -U centraldsp centraldsp | gzip > /backup/dsp-$(date +%F).sql.gz

# Redis (снапшот RDB) — счётчики бюджетов/капов
docker compose exec -T redis redis-cli BGSAVE
```

## 10. Что мониторить (минимум)

| Проверка | Как | Порог тревоги |
|---|---|---|
| Backend health | `GET /health` раз в минуту | нет ответа 2 мин |
| Биддер health | `GET /healthz` | `feed_age > 120s` или redis down |
| Диск | `df -h` | > 80% |
| p99 время ответа биддера | лог биддера | > 80 мс |
| Баланс рекламодателей | UI «Финансы» | близок к 0 — кампании встанут |

Полноценный контур (Prometheus + Grafana + алерты) — следующий этап,
см. `docs/PROD_RUNBOOK.md`.

## 11. Типичные проблемы

| Симптом | Причина | Решение |
|---|---|---|
| Биддер NOBID на всё | пустой фид (нет активных кампаний/креативов с erid) | проверьте `GET /healthz` биддера и статусы кампаний |
| 401 на фиде | `RTB_API_TOKEN` не совпадает у backend и биддера | одинаковое значение в `.env`, перезапуск |
| Кампания не стартует | нет одобренного креатива или баланс 0 | одобрить креатив, пополнить баланс |
| ОРД-ошибки при создании креатива | неверный API-ключ или прод вместо песочницы | «Настройки -> ОРД Озон -> Проверить связь» |
| 429 на API | сработал rate limit (300 req/min на пользователя) | это защита; для интеграций поднять лимит в `main.py` |
