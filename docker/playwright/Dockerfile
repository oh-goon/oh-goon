# syntax=docker/dockerfile:1.7
ARG PLAYWRIGHT_IMAGE
FROM node:24.19.0-bookworm-slim AS node-runtime
FROM ghcr.io/astral-sh/uv:0.10.0 AS uv-bin
FROM ${PLAYWRIGHT_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG PLAYWRIGHT_VERSION
COPY --from=node-runtime /usr/local/ /usr/local/
COPY --from=uv-bin /uv /uvx /usr/local/bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git make python3.12 \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global npm@10.9.2 --ignore-scripts --no-audit --no-fund \
    && test "$(npm --version)" = "10.9.2"

WORKDIR /opt/oh-goon
COPY . .
ENV CI=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    UV_PYTHON=/usr/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never
RUN test "$(uv run --locked --project backend oh-goon-browser-acceptance-container version)" = "$PLAYWRIGHT_VERSION"
RUN make setup

CMD ["make", "acceptance-release"]
