FROM node:24-alpine AS web-build
WORKDIR /workspace/apps/web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EDGAR_MOE_DEMO_SNAPSHOT=/app/data/demo/snapshot.json
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY data/demo ./data/demo
COPY --from=web-build /workspace/apps/web/dist ./static
EXPOSE 8000
CMD ["uvicorn", "edgar_moe.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
