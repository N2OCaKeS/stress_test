#!/usr/bin/env bash
set -euo pipefail

export POSTGRES_DB="${POSTGRES_DB:-app_db}"
export POSTGRES_USER="${POSTGRES_USER:-app_user}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-app_password}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export PGDATA="${PGDATA:-/var/lib/postgresql/data}"

POSTGRES_BIN_DIR="$(pg_config --bindir)"

mkdir -p "${PGDATA}"
chown -R postgres:postgres /var/lib/postgresql
chmod 700 "${PGDATA}"

if [[ ! -s "${PGDATA}/PG_VERSION" ]]; then
  su postgres -c "\"${POSTGRES_BIN_DIR}/initdb\" -D \"${PGDATA}\""

  {
    echo "listen_addresses = '*'"
    echo "port = ${POSTGRES_PORT}"
  } >> "${PGDATA}/postgresql.conf"

  {
    echo "host all all all scram-sha-256"
    echo "host replication all all scram-sha-256"
  } >> "${PGDATA}/pg_hba.conf"

  su postgres -c "\"${POSTGRES_BIN_DIR}/pg_ctl\" -D \"${PGDATA}\" -w start"

  su postgres -c "psql -v ON_ERROR_STOP=1 --dbname postgres <<'SQL'
DO
\$do\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${POSTGRES_USER}') THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '${POSTGRES_USER}', '${POSTGRES_PASSWORD}');
   ELSE
      EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', '${POSTGRES_USER}', '${POSTGRES_PASSWORD}');
   END IF;
END
\$do\$;
SQL"

  su postgres -c "psql -v ON_ERROR_STOP=1 --dbname postgres <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', '${POSTGRES_DB}', '${POSTGRES_USER}')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = '${POSTGRES_DB}'
)\gexec
SQL"

  su postgres -c "\"${POSTGRES_BIN_DIR}/pg_ctl\" -D \"${PGDATA}\" -m fast -w stop"
fi

exec su postgres -c "\"${POSTGRES_BIN_DIR}/postgres\" -D \"${PGDATA}\" -c listen_addresses='*' -p \"${POSTGRES_PORT}\""
