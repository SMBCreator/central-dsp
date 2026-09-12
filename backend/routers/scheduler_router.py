"""Управление расписанием фоновых задач (audit O-01, C1b).

Таблица `scheduled_jobs` исполняется сервисом `scheduler` (backend/scheduler.py).
Здесь — только чтение и правка расписания админом: включить/выключить,
сменить cron-выражение (UTC) или таймаут, запустить немедленно. Сам запуск
делает scheduler на ближайшем тике, backend задачу не выполняет.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cronexpr import CronError, CronExpr
from db import get_pool
from rbac import require_admin
from scheduler import JOB_COLUMNS, ensure_seed

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class JobPatch(BaseModel):
    enabled: bool | None = None
    schedule: str | None = Field(default=None, max_length=100)
    timeout_seconds: int | None = Field(default=None, ge=5, le=21600)


def _row(r) -> dict:
    d = dict(r)
    d["next_run_in_seconds"] = (
        max(0, int((d["next_run_at"] - datetime.now(UTC)).total_seconds())) if d.get("next_run_at") else None
    )
    return d


@router.get("/jobs", dependencies=[Depends(require_admin)])
async def list_jobs():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await ensure_seed(conn)
        rows = await conn.fetch(f"SELECT {JOB_COLUMNS} FROM scheduled_jobs ORDER BY name")
        last_tick = await conn.fetchval("SELECT max(last_finished_at) FROM scheduled_jobs")
    return {"jobs": [_row(r) for r in rows], "last_job_finished_at": last_tick}


@router.patch("/jobs/{name}", dependencies=[Depends(require_admin)])
async def patch_job(name: str, body: JobPatch):
    sets, params = [], [name]
    if body.schedule is not None:
        try:
            schedule = CronExpr.parse(body.schedule).text
        except CronError as e:
            raise HTTPException(status_code=422, detail=f"schedule: {e}") from None
        params.append(schedule)
        sets.append(f"schedule = ${len(params)}")
        sets.append("next_run_at = NULL")  # scheduler пересчитает на ближайшем тике
    if body.enabled is not None:
        params.append(body.enabled)
        sets.append(f"enabled = ${len(params)}")
        if body.enabled:
            sets.append("next_run_at = NULL")  # не запускать «за пропущенное» сразу после включения
    if body.timeout_seconds is not None:
        params.append(body.timeout_seconds)
        sets.append(f"timeout_seconds = ${len(params)}")
    if not sets:
        raise HTTPException(status_code=422, detail="nothing to update")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE scheduled_jobs SET {', '.join(sets)}, updated_at = now() WHERE name = $1 RETURNING {JOB_COLUMNS}",
            *params,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _row(row)


@router.post("/jobs/{name}/run", dependencies=[Depends(require_admin)])
async def run_job_now(name: str):
    """Поставить задачу в очередь «сейчас»: scheduler подхватит на ближайшем
    тике (SCHEDULER_TICK, по умолчанию 15 с). Выключенная задача не запускается."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE scheduled_jobs SET next_run_at = now(), updated_at = now() "
            f"WHERE name = $1 AND enabled AND locked_at IS NULL RETURNING {JOB_COLUMNS}",
            name,
        )
        if row is None:
            exists = await conn.fetchrow("SELECT enabled, locked_at FROM scheduled_jobs WHERE name = $1", name)
    if row is None:
        if exists is None:
            raise HTTPException(status_code=404, detail="job not found")
        if not exists["enabled"]:
            raise HTTPException(status_code=409, detail="job is disabled")
        raise HTTPException(status_code=409, detail="job is running")
    return _row(row)
