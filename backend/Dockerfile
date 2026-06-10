FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source into /app
# The source tree is: backend/ -> /app/
# so 'from backend.config import settings' needs Python to see /
# as a package root, making /app the 'backend' package.
COPY . /app/

# Create /backend symlink so 'import backend' resolves to /app
# This is the cleanest fix: no code changes needed anywhere.
RUN ln -s /app /backend

# PYTHONPATH=/ means Python sees both / and /app
# 'import backend' -> finds /backend -> symlink to /app -> works
# 'import config'  -> finds /app/config.py -> works
ENV PYTHONPATH=/

# Default command (overridden per service in docker-compose)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
