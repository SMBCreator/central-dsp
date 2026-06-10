FROM python:3.11-slim

# /app is the Python root - 'import backend' resolves to /app/backend/
WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy source into /app/backend/ so 'from backend.x import y' works
COPY . /app/backend/

# PYTHONPATH=/app → Python finds /app/backend as the 'backend' package
ENV PYTHONPATH=/app

# Run uvicorn from /app/backend where main.py lives
WORKDIR /app/backend

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
