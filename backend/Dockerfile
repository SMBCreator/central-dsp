FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
# --no-cache-dir ensures fresh install, no layer-cache skipping packages
RUN pip install --no-cache-dir -r /app/requirements.txt
# Explicitly install WS libs so uvicorn[standard] extras are always present
RUN pip install --no-cache-dir websockets==12.0 wsproto==1.2.0 user-agents==2.2.0

# Copy all backend source directly into /app
# so `from config import settings`, `from database import ...` etc work
COPY . /app/

ENV PYTHONPATH=/app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
