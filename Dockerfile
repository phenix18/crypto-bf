# syntax=docker/dockerfile:1

# ---------- Stage 1: build the SPA ----------
FROM node:20-slim AS frontend
WORKDIR /build
COPY service/frontend/package.json service/frontend/package-lock.json ./
RUN npm ci
COPY service/frontend/ ./
RUN npm run build

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY service/requirements.txt service/requirements.txt
RUN pip install --upgrade pip && pip install -r service/requirements.txt

# The server modules import each other flat (`from database import ...`), so the
# server directory has to be on the import path.
ENV PYTHONPATH=/app/service/server

# routes_misc.py resolves skills/ from the project root and the built SPA from
# service/frontend/dist, so the repository layout is preserved inside the image.
COPY service/server/ service/server/
COPY skills/ skills/
COPY docs/ docs/
COPY --from=frontend /build/dist service/frontend/dist

RUN mkdir -p service/server/data service/server/logs

WORKDIR /app/service/server
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
