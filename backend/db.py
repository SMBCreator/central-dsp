"""asyncpg connection pool (audit O-07 / C6).

Один пул на процесс. Создание под asyncio.Lock: без него первые параллельные
запросы после старта (healthcheck + consumer + UI) успевали каждый создать свой
пул, и лишние соединения жили до конца процесса. close_pool() вызывается из
lifespan при остановке — graceful shutdown вместо обрыва TCP по SIGTERM.
"""
import asyncio
import os

import asyncpg

_pool: asyncpg.Pool | None = None
_pool_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    # Lock создаётся лениво внутри работающего loop: модуль импортируется
    # до старта uvicorn, а asyncio.Lock() на import-time привязался бы не к тому loop.
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _lock():
        if _pool is None:
            _pool = await asyncpg.create_pool(
                dsn=os.environ["DATABASE_URL"],
                min_size=1,
                max_size=int(os.environ.get("DB_POOL_MAX", "10")),
                command_timeout=30,
            )
    return _pool


async def close_pool() -> None:
    """Закрывает пул и сбрасывает singleton. Идемпотентно; ошибки закрытия
    (например, БД уже недоступна) не должны ронять shutdown."""
    global _pool
    pool, _pool = _pool, None
    if pool is None:
        return
    try:
        await asyncio.wait_for(pool.close(), timeout=10)
    except Exception:  # noqa: BLE001 — на shutdown важнее выйти, чем идеально закрыть
        pool.terminate()
