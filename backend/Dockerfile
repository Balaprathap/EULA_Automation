# One image, two entrypoints: the API and the analysis worker run the same
# code with different commands, so they can never drift apart.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[dev]" || pip install -e .

COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY evaluation ./evaluation
COPY tests ./tests

# Never run as root.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

# PORT is injected by the host platform (Render sets it, typically 10000) and
# falls back to 8000 for docker-compose and local runs.
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# Shell form so ${PORT} is expanded at runtime. Exec form would pass the literal
# string "$PORT" to uvicorn and the service would never bind, which makes the
# container fail platform port detection.
# docker-compose overrides this command for the worker entrypoint.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
