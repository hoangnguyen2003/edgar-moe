FROM node:24-alpine AS web-build
WORKDIR /workspace/apps/web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM python:3.12-slim AS python-dependencies
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml README.md LICENSE uv.lock ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.13 \
    && uv sync --frozen --no-dev --no-editable \
    && rm -rf /root/.cache

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EDGAR_MOE_DEMO_SNAPSHOT=/app/data/demo/snapshot.json \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app \
       --shell /usr/sbin/nologin app
COPY --from=python-dependencies --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app data/demo ./data/demo
COPY --from=web-build --chown=app:app /workspace/apps/web/dist ./static
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]
CMD ["uvicorn", "edgar_moe.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
