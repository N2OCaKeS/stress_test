#!/usr/bin/env bash
set -euo pipefail

cd /workspace/dbos_server_manager

if command -v pg_isready >/dev/null 2>&1; then
  until pg_isready -h "${POSTGRES_HOST:-postgres}" -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-app_user}" >/dev/null 2>&1; do
    echo "Waiting for PostgreSQL..."
    sleep 2
  done
fi

for service_dir in auth_service config_service logging_service server_service; do
  if [[ -f "${service_dir}/alembic.ini" ]]; then
    echo "Applying Alembic migrations for ${service_dir}..."
    (
      cd "/workspace/dbos_server_manager/${service_dir}"
      alembic upgrade head
    )
  fi
done
