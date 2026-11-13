# Build context is the repo root:  docker build -f gateway/Dockerfile .
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /build
COPY gateway/pyproject.toml gateway/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" GATEWAY_STATIC_DIR=/app/static
COPY gateway/gateway/ gateway/
COPY gateway/examples/ examples/
COPY gateway/catalog.yaml .
COPY --from=web /web/dist/ static/
RUN useradd -m appuser
USER appuser
EXPOSE 8090
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8090"]
