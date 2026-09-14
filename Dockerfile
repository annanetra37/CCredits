# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a code change does not reinstall the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY cli/ ./cli/
COPY scripts/ ./scripts/

# Only used when STORAGE_BACKEND=local. On Railway this directory is ephemeral,
# which is why production should point at object storage instead.
RUN mkdir -p /app/var/uploads

EXPOSE 8000

# Railway injects PORT at runtime; honour it and fall back to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
