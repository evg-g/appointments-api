# Multi-stage build.
#   Stage 1 (builder) resolves the locked dependencies with uv into a self-contained venv.
#   Stage 2 (runtime) is a slim image that copies only that venv + the source and runs as a
#   non-root user. No build tools, no uv, and no dev dependencies reach the runtime image.
#
# Why multi-stage: the final image ships the app and its runtime deps and nothing else, which
# keeps it small and shrinks the attack surface that the Trivy image scan (see .github/workflows)
# has to clear.

FROM python:3.12-slim AS builder

# Optional corporate-CA support (off by default; see docs/CORPORATE_NETWORK.md). Only needed for
# local builds behind a TLS-inspecting proxy; GitHub-hosted runners have a clean TLS path.
ARG EXTRA_CA_CERT=""
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Install dependencies first, in their own layer, so a source-only change does not re-resolve
# the whole dependency graph. --no-install-project keeps the app itself out of this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

# Build metadata, passed by CI (cd.yml). Recorded as OCI image labels so a running image can be
# traced back to the exact commit and build. Defaults keep a bare `docker build` working.
ARG VERSION="0.0.0-dev"
ARG REVISION="unknown"
ARG BUILD_DATE="1970-01-01T00:00:00Z"
ARG EXTRA_CA_CERT=""

LABEL org.opencontainers.image.title="appointments-api" \
      org.opencontainers.image.description="Aurora Clinic appointment scheduling and cold-chain monitoring API" \
      org.opencontainers.image.source="https://github.com/evg-g/appointments-api" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${BUILD_DATE}"

RUN if [ -n "$EXTRA_CA_CERT" ]; then \
        echo "$EXTRA_CA_CERT" > /usr/local/share/ca-certificates/extra.crt && \
        update-ca-certificates; \
    fi && \
    groupadd --system app && useradd --system --gid app --home /app app

ENV PATH="/app/.venv/bin:$PATH" \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_VERSION="${VERSION}"

WORKDIR /app
# Copy the built venv and source from the builder, owned by the unprivileged user.
COPY --from=builder --chown=app:app /app /app
USER app

EXPOSE 8000
# Liveness probe for orchestrators that read the image HEALTHCHECK (Compose, some runtimes).
# Container Apps uses its own probe config (see infra/main.bicep) instead.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health/live').status==200 else sys.exit(1)"

# Exec form so uvicorn is PID 1 and receives SIGTERM directly for a clean shutdown (the lifespan
# disposes the DB engine and Redis client).
CMD ["uvicorn", "appointments_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
