# Central DSP management API (FastAPI). Тот же образ использует сервис
# scheduler (compose.yml) — планировщик /cron/* без Celery.
#
# Audit O-07 / C6:
#   * базовый образ закреплён patch-версией + digest (обновлять осознанно,
#     отдельным PR — docs/DEPLOYMENT.md §13);
#   * зависимости ставятся из requirements.lock с проверкой sha256 (только wheels);
#   * процесс работает от app (10001:10001); root нужен только entrypoint'у,
#     чтобы починить владельца legacy-volume с креативами (docker-entrypoint.sh).
FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/app \
    UPLOAD_DIR=/data/uploads

RUN groupadd --system --gid "${APP_GID}" app \
 && useradd --system --uid "${APP_UID}" --gid "${APP_GID}" \
      --home-dir /home/app --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data/uploads \
 && chown "${APP_UID}:${APP_GID}" /data/uploads

WORKDIR /app

# Deps first for layer caching. --require-hashes: любое расхождение с lock — ошибка сборки.
COPY requirements.lock ./
RUN pip install --require-hashes --no-deps --only-binary=:all: -r requirements.lock

# Код принадлежит root и read-only для app: процесс не может переписать себя.
COPY --chown=root:root . .
RUN chmod 0755 /app/docker-entrypoint.sh

EXPOSE 8000

# Liveness через setpriv, чтобы и probe не работал от root (см. compose.yml healthcheck).
ENTRYPOINT ["/app/docker-entrypoint.sh"]
# Apply SQL migrations, then start the API. exec — uvicorn становится PID 1 и сам
# обрабатывает SIGTERM; --timeout-graceful-shutdown < stop_grace_period compose.
CMD ["sh", "-c", "python migrate.py && exec uvicorn main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 20"]
