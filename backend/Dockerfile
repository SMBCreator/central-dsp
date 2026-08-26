# CentralDSP management API (FastAPI + Celery worker/beat share this image).
FROM python:3.12-slim

WORKDIR /app

# Deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir . uvicorn celery flower

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Apply SQL migrations, then start the API.
CMD ["sh", "-c", "python migrate.py && uvicorn main:app --host 0.0.0.0 --port 8000"]
