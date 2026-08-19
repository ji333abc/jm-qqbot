FROM node:22-bookworm-slim AS node-deps
WORKDIR /build/uploader
COPY uploader/package.json uploader/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QQBOT_JM_TEMP_ROOT=/app/data/jm-tasks \
    QQBOT_JM_TIMING_PATH=/app/data/jm-timing.json \
    QQBOT_JM_UPLOADER=/app/uploader/uploader.mjs

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY jm_qqbot ./jm_qqbot
COPY uploader ./uploader
COPY --from=node-deps /build/uploader/node_modules ./uploader/node_modules

RUN pip install --no-cache-dir . \
    && groupadd --system bot \
    && useradd --system --gid bot --home-dir /app bot \
    && mkdir -p /app/data/jm-tasks \
    && chown -R bot:bot /app

USER bot
VOLUME ["/app/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]
CMD ["jm-qqbot"]
