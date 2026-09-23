FROM node:22-alpine AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STUDIO_WEB_DIST=/app/web/dist \
    STUDIO_THUMBNAIL_DIR=/data/thumbnails
WORKDIR /app
COPY pyproject.toml ./
COPY flamoris_studio/ ./flamoris_studio/
COPY alembic.ini ./
COPY alembic/ ./alembic/
RUN pip install --no-cache-dir . && \
    mkdir -p /data/thumbnails && \
    chown -R 10001:10001 /data/thumbnails
COPY --from=web /build/web/dist/ ./web/dist/
USER 10001:10001
EXPOSE 5087
CMD ["uvicorn", "flamoris_studio.app:production_app", "--factory", "--host", "0.0.0.0", "--port", "5087", "--proxy-headers", "--forwarded-allow-ips", "*"]
