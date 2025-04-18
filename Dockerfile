# syntax = docker/dockerfile:1.4

FROM python:3.11.2-slim-bullseye AS base

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy
ENV PATH=/bin:/usr/bin:/usr/sbin:/usr/local/bin
# Make uv install to system rather in a virtual environment
ENV UV_PROJECT_ENVIRONMENT=/usr/local

# configure python
ENV \
    # See https://stackoverflow.com/questions/59732335/is-there-any-disadvantage-in-using-pythondontwritebytecode-in-docker
    PYTHONDONTWRITEBYTECODE=1 \
    # See https://stackoverflow.com/questions/59812009/what-is-the-use-of-pythonunbuffered-in-docker-file
    PYTHONUNBUFFERED=1

# Update base system & install system dependencies
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -qy dist-upgrade \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
    && rm -rf /var/lib/apt/lists/*

# Install UV
RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s -- -v

ENV PATH="/root/.local/bin/:$PATH"

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

ADD . /app

FROM base

WORKDIR /app

COPY dagster-nomad /app/dagster-nomad

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

COPY user_code/ user_code/

COPY dagster/dagster.yaml dagster/dagster.yaml
ENV DAGSTER_HOME=/app/dagster

COPY entries/dagster_job.sh /dagster_job.sh

ENTRYPOINT ["/dagster_job.sh"]
