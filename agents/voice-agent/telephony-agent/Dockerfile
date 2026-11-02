# Streaming Telephony Voice Agent — production image
#
# Single process by design: call state, usage and the dashboard event bus are
# in-memory and per-process. Never add `--workers`, `--replicas` or a load
# balancer with multiple instances of this container.
#
# Python 3.12 base; the `audioop-lts` requirement entry is a 3.13-only marker
# and is simply skipped here.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so code edits do not invalidate the caching layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + static dashboard. Junk (venv, caches, secrets) is excluded via
# .dockerignore — the image must never contain credentials.
COPY . .

EXPOSE 8000

# Plain HTTP. Plivo webhooks need a public HTTPS origin in front of the
# container (LB / reverse proxy / tunnel); set PUBLIC_BASE_URL in .env to
# whatever that origin is.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
