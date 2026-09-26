# Multi-stage build. Stage 1 resolves dependencies with uv; stage 2 is a slim runtime that
# runs as a non-root user. Kept minimal for milestone 1; hardened further in milestone 6.

FROM python:3.12-slim AS builder

# Optional corporate-CA support (off by default; see docs/CORPORATE_NETWORK.md).
ARG EXTRA_CA_CERT=""
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

ARG EXTRA_CA_CERT=""
RUN if [ -n "$EXTRA_CA_CERT" ]; then \
        echo "$EXTRA_CA_CERT" > /usr/local/share/ca-certificates/extra.crt && \
        update-ca-certificates; \
    fi && \
    groupadd --system app && useradd --system --gid app --home /app app

ENV PATH="/app/.venv/bin:$PATH" \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --from=builder --chown=app:app /app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')"
CMD ["uvicorn", "appointments_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
