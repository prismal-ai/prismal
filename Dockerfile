# syntax=docker/dockerfile:1
# ============================================================
# Prismal — container image (published to GHCR)
# Multi-stage: build the wheel + install all deps into a venv (with the C
# toolchain available), then copy only that venv into a slim runtime — no
# build tools, no uv image dependency. Base install only (no heavy extras);
# derive an image and `pip install "prismal-ai[all]"` if you need them.
# ============================================================

FROM python:3.13-slim AS builder
ENV PIP_NO_CACHE_DIR=1
# Toolchain for any base dependency shipped only as an sdist (no cp313 wheel).
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
# Build our wheel (pure-Python, hatchling) and install it + deps into /opt/venv.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip build \
 && /opt/venv/bin/python -m build --wheel --outdir /tmp/dist \
 && /opt/venv/bin/pip install /tmp/dist/*.whl

FROM python:3.13-slim AS runtime
LABEL org.opencontainers.image.title="prismal" \
      org.opencontainers.image.description="Prismal — LangGraph supervisor agent framework" \
      org.opencontainers.image.source="https://github.com/prismal-ai/prismal" \
      org.opencontainers.image.licenses="MIT"
# Copy only the built virtualenv from the builder (no compilers in the runtime).
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 1000 prismal
USER prismal
WORKDIR /home/prismal
# Default: list installed plugins; override e.g. `docker run <image> -m prismal.plugins doctor`
ENTRYPOINT ["python"]
CMD ["-m", "prismal.plugins", "list"]
