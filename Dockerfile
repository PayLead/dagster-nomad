# syntax = docker/dockerfile:1.4

FROM python:3.11.2-slim-bullseye AS base

ARG POETRY_VERSION=1.3.1
ARG POETRY_HOME=/opt/poetry

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

# install & configure poetry
RUN python -m venv ${POETRY_HOME} \
    && ${POETRY_HOME}/bin/pip install poetry==${POETRY_VERSION} \
    && touch ${POETRY_HOME}/poetry_env \
    && ln -s ${POETRY_HOME}/bin/poetry /usr/local/bin/poetry
ENV \
    POETRY_VIRTUALENVS_CREATE=false


FROM base

WORKDIR /app

COPY pyproject.toml poetry.lock /app/
COPY dagster-nomad /app/dagster-nomad
RUN --mount=type=cache,target=/root/.cache,sharing=locked poetry install --no-root

COPY user_code/ user_code/

COPY dagster/dagster.yaml dagster/dagster.yaml
ENV DAGSTER_HOME=/app/dagster

COPY entries/dagster_job.sh /dagster_job.sh

ENTRYPOINT ["/dagster_job.sh"]
