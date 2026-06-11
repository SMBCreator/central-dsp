FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
# --no-cache-dir ensures fresh install, no layer-cache skipping packages
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy all backend source directly into /app
# so `from config import settings`, `from database import ...` etc work
COPY . /app/

ENV PYTHONPATH=/app

# No --reload in container:
#   - --reload spawns WatchFiles subprocess that sends WS upgrade requests
#     causing "No supported WebSocket library" spam
#   - WatchFiles monitors /app volume endlessly, wasting CPU
#   - 2 workers handle concurrent requests (bidder + frontend polls)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
