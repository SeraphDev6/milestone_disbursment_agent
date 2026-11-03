# The whole app in one image: the API (FastAPI) serving the built web app at /app.
#   docker compose up --build        -> http://localhost:8000/app/

# --- web: build the React app to web/dist ----------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# The OpenAPI types are generated from the backend and committed (src/api/schema.d.ts), so no Python here.
RUN npm run build

# --- api: Python runtime ----------------------------------------------------------------------------
FROM python:3.12-slim AS api
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /srv

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations/ migrations/
COPY app/ app/
COPY --from=web /src/web/dist web/dist

RUN useradd --system --uid 10001 mde && chown -R mde /srv
USER mde
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
