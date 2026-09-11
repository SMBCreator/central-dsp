-- Audit O-01 (план C1b): расписание фоновых задач живёт в БД, исполняет его
-- сервис `scheduler` (backend/scheduler.py, тот же образ, что backend). Он
-- дёргает /cron/* по HTTP с Bearer CRON_SECRET — сами задачи не меняются.
-- Строки сеет scheduler.ensure_seed из DEFAULT_JOBS (ON CONFLICT DO NOTHING):
-- правки админа (schedule/enabled) сохраняются между релизами.
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    name              text PRIMARY KEY,                       -- = obs_metrics.CRON_JOBS
    path              text NOT NULL,                          -- /cron/<route>
    schedule          text NOT NULL,                          -- 5 полей cron, UTC
    enabled           boolean NOT NULL DEFAULT true,
    timeout_seconds   integer NOT NULL DEFAULT 600 CHECK (timeout_seconds BETWEEN 5 AND 21600),
    description       text NOT NULL DEFAULT '',
    next_run_at       timestamptz,                            -- NULL = пересчитать из schedule
    -- lease: занят экземпляром scheduler'а; протухает через 2×timeout
    locked_at         timestamptz,
    locked_by         text,
    last_run_at       timestamptz,
    last_finished_at  timestamptz,
    last_status       text CHECK (last_status IN ('ok', 'error', 'timeout', 'unreachable')),
    last_http_status  integer,
    last_duration_ms  integer,
    last_error        text,
    runs_total        bigint NOT NULL DEFAULT 0,
    errors_total      bigint NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_due
    ON scheduled_jobs (next_run_at)
    WHERE enabled;
