FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PGDATA=/var/lib/postgresql/data

RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/lib/postgresql/data \
    && chown -R postgres:postgres /var/lib/postgresql

COPY .devcontainer/postgres/entrypoint.sh /usr/local/bin/devcontainer-postgres-entrypoint.sh
RUN chmod +x /usr/local/bin/devcontainer-postgres-entrypoint.sh

EXPOSE 5432

ENTRYPOINT ["/usr/local/bin/devcontainer-postgres-entrypoint.sh"]
