# syntax=docker/dockerfile:1
# ============================================================
# Prismal — container image (published to GHCR)
# Multi-stage: build the wheel + install all deps into a venv (with the C
# toolchain available), then copy only that venv into a slim runtime — no
# build tools, no uv image dependency. Base install only (no heavy extras);
# derive an image and `pip install "prismal-ai[all]"` if you need them.
#
# Supply chain: dependencies are installed from uv.lock (`uv sync --frozen`,
# hash-verified), never re-resolved from PyPI at build time. An unpinned `pip install <wheel>`
# picks whatever is latest on the index — e.g. the compromised litellm 1.107.0
# (2026-10-21) for anyone building inside that window.
# ============================================================

FROM python:3.13-slim AS builder
ENV PIP_NO_CACHE_DIR=1
# Toolchain for any base dependency shipped only as an sdist (no cp313 wheel).
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
# uv lives only in the builder (pinned; the runtime never sees it). `uv sync
# --frozen` installs exactly uv.lock — hash-verified, honouring the
# [tool.uv] overrides that pip would not see — plus our project as a regular
# (non-editable) wheel into /opt/venv. Base deps only: no extras, no dev.
ARG UV_VERSION=0.11.7
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN pip install "uv==${UV_VERSION}" \
 && uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python3.13 \
 && /opt/venv/bin/python -c "import importlib.metadata as m; print('litellm', m.version('litellm'))"

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
