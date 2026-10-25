# syntax=docker/dockerfile:1.7
FROM node:24.19.0-bookworm-slim AS frontend-build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json* frontend/
RUN test -f frontend/package-lock.json || { echo 'frontend/package-lock.json is required; run make locks and commit both release locks.' >&2; exit 2; }
RUN npm install --global npm@10.9.2 --ignore-scripts --no-audit --no-fund \
    && test "$(npm --version)" = "10.9.2"
RUN cd frontend && npm ci --prefer-offline --ignore-scripts --no-audit --no-fund
COPY frontend/ frontend/
WORKDIR /src/frontend
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.12.5 AS uv-bin
FROM ubuntu:24.04 AS wheel-build
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 ca-certificates git curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv-bin /uv /uvx /usr/local/bin/
WORKDIR /opt/oh-goon
COPY VERSION VERSION
COPY backend/pyproject.toml backend/uv.lock backend/
ENV UV_PYTHON=/usr/bin/python3.12 UV_PYTHON_DOWNLOADS=never SOURCE_DATE_EPOCH=1580601600
RUN uv sync --locked --project backend --group dev --group quality --group build --no-install-project --no-python-downloads
COPY backend/src backend/src
COPY backend/hatch_build.py backend/hatch_build.py
COPY templates templates
COPY --from=frontend-build /src/frontend/dist generated/release/static
RUN uv sync --locked --project backend --group dev --group quality --group build --no-python-downloads \
    && uv run --project backend --offline --frozen --no-sync oh-goon-release-wheel-build
RUN uv export --project backend --frozen --no-dev --no-emit-project --format requirements.txt \
    --no-annotate --no-header --output-file generated/release/runtime-requirements.txt

FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 ca-certificates git curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv-bin /uv /uvx /usr/local/bin/
WORKDIR /opt/oh-goon
ENV UV_PYTHON=/usr/bin/python3.12 UV_PYTHON_DOWNLOADS=never UV_NO_DEV=1
RUN uv venv backend/.venv --python /usr/bin/python3.12 --no-python-downloads
COPY --from=wheel-build /opt/oh-goon/generated/release/runtime-requirements.txt /tmp/runtime-requirements.txt
RUN uv pip install --python backend/.venv/bin/python --require-hashes --no-python-downloads -r /tmp/runtime-requirements.txt \
    && rm /tmp/runtime-requirements.txt
COPY --from=wheel-build /opt/oh-goon/generated/release/dist/oh_goon-*-py3-none-any.whl /tmp/oh-goon.whl
RUN uv pip install --python backend/.venv/bin/python --no-deps --no-python-downloads /tmp/oh-goon.whl \
    && rm /tmp/oh-goon.whl
ENV PATH="/opt/oh-goon/backend/.venv/bin:${PATH}" \
    OH_GOON_CONTAINER=1 \
    OH_GOON_DB=/var/lib/oh-goon/oh-goon.db \
    OH_GOON_LEAGUE_FILE=/var/lib/oh-goon/league.yml \
    HOME=/var/lib/oh-goon/home
RUN mkdir -p /var/lib/oh-goon /repositories && chown -R 1000:1000 /var/lib/oh-goon /repositories
USER 1000:1000
EXPOSE 8787
HEALTHCHECK --interval=10s --timeout=2s --start-period=10s --retries=5 \
    CMD curl --fail --silent --max-time 1 http://127.0.0.1:8787/healthz >/dev/null || exit 1
CMD ["oh-goon-container"]
