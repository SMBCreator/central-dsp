# 📝 Лог выполненных шагов

Здесь отмечаем каждый выполненный шаг с датой и описанием что изменилось.

---

## Выполнено

| Дата | Шаг | Файлы | Описание |
|---|---|---|---|
| 2026-05-30 | — | `backend/models.py` | 15 таблиц, FK, индексы |
| 2026-05-30 | — | `backend/schemas.py` | Pydantic v2 схемы |
| 2026-05-30 | — | `backend/main.py` | FastAPI роутеры |
| 2026-05-30 | — | `backend/auth.py` + `security.py` | JWT авторизация |
| 2026-05-30 | — | `backend/rtb_bridge/` | RTB bridge заготовка |
| 2026-05-30 | — | `backend/tasks/` | Celery задачи скелет |
| 2026-06-01 | — | `backend/bid_optimizer.py` | Исправлены импорты |
| 2026-06-01 | — | `backend/db/migrations/` | Alembic миграция всех таблиц |
| 2026-06-01 | — | `bidder/src/main/resources/application.properties` | Конфиг RTB4FREE под наш стек |
| 2026-06-03 | — | `SMBCreator/bidder` | Форк RTB4FREE bidder |
| 2026-06-03 | — | `docs/ROADMAP.md` | Мастер-план 37 шагов |

---

## В работе

| Шаг | Файл | Кто делает |
|---|---|---|
| 1.1 | `bidder/Dockerfile` | Следующий |
| 1.2 | `backend/main.py` (rtb_bridge) | Следующий |

---

## Очередь

См. [ROADMAP.md](./ROADMAP.md) для полного списка.
