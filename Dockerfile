# syntax=docker/dockerfile:1
# ============================================================
# Prismal — container image (published to GHCR)
# Multi-stage: build the wheel with uv, install it into a slim runtime.
# Base install only (no heavy extras) to keep the image small; pass
# `pip install "prismal-ai[all]"` in a derived image if you need extras.
# ============================================================

FROM python:3.13-slim AS builder
ENV UV_LINK_MODE=copy
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
# uv from its official image (pinned by tag; bump as needed)
COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /usr/local/bin/uv
WORKDIR /src
COPY . .
RUN uv build --wheel --out-dir /dist

FROM python:3.13-slim AS runtime
LABEL org.opencontainers.image.title="prismal" \
      org.opencontainers.image.description="Prismal — LangGraph supervisor agent framework" \
      org.opencontainers.image.source="https://github.com/prismal-ai/prismal" \
      org.opencontainers.image.licenses="MIT"
# Non-root runtime user
RUN useradd --create-home --uid 1000 prismal
COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl
USER prismal
WORKDIR /home/prismal
# Default: show the plugin doctor; override `docker run <image> -m prismal.plugins list`
ENTRYPOINT ["python"]
CMD ["-m", "prismal.plugins", "doctor"]
