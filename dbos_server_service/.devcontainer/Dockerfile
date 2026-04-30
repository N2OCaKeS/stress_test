FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/dbos_server_manager

# The base devcontainers image currently ships a Yarn apt source that may
# fail signature validation on fresh machines. We don't install Yarn via apt
# here, so removing that source keeps apt-get update deterministic.
RUN rm -f /etc/apt/sources.list.d/yarn.list

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        postgresql-client \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN su vscode -c "bash -lc 'source /usr/local/share/nvm/nvm.sh && nvm install --lts && nvm alias default lts/*'"

WORKDIR /workspace/dbos_server_manager
