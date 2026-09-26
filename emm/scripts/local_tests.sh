#!/usr/bin/env bash
# Локальный прогон unit-тестов сервиса без Docker (облачные сессии агентов,
# машины без docker daemon). Использует локальные Postgres 16 и Redis и
# общие venv в /opt/venvs/<service>. Каждый агент задаёт свою БД через
# TEST_DB_SUFFIX, чтобы параллельные прогоны не мешали друг другу.
#
#   scripts/local_tests.sh <service> [pytest args...]
#   TEST_DB_SUFFIX=agent1 scripts/local_tests.sh testing_service tests/test_queue.py
# (-q уже есть в addopts каждого pyproject; второй -q прячет итоговую строку
#  "N passed", поэтому не добавлять его)
#
# Подготовка окружения (один раз на машину):
#   pg_ctlcluster 16 main start; redis-server --daemonize yes
#   psql -U postgres -c "create role app_user login superuser password 'app_password'"
#   python3.12 -m venv /opt/venvs/<service>
#   (cd <service> && VIRTUAL_ENV=/opt/venvs/<service> PATH=/opt/venvs/<service>/bin:$PATH poetry install --no-root)
set -euo pipefail

service="${1:?usage: $0 <service> [pytest args...]}"
shift || true
root="$(cd "$(dirname "$0")/.." && pwd)"
venv="${VENV_ROOT:-/opt/venvs}/${service}"
suffix="${TEST_DB_SUFFIX:-local}"
db="test_${service}_${suffix}"
url="postgresql+psycopg://app_user:app_password@localhost:5432/${db}"

[ -x "${venv}/bin/pytest" ] || { echo "no venv at ${venv} (see header of $0)" >&2; exit 2; }

pg_ctlcluster 16 main start >/dev/null 2>&1 || true
redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes --dir /tmp --save "" >/dev/null
if [ "${service}" != "testing_worker" ]; then
  if ! PGPASSWORD=app_password psql -h localhost -U app_user -d postgres -tAc "select 1 from pg_database where datname='${db}'" | grep -q 1; then
    PGPASSWORD=app_password psql -h localhost -U app_user -d postgres -qc "create database ${db}"
  fi
fi

export PATH="${venv}/bin:${PATH}" PYTHONPATH=. APP_ENV=test \
  DATABASE_URL="${url}" TEST_DATABASE_URL="${url}" \
  AUTH_SERVICE_URL=http://not-used SERVER_SERVICE_URL=http://not-used LOGGING_SERVICE_URL=http://not-used

case "${service}" in
  testing_service) export REDIS_URL=redis://localhost:6379/3 ;;
  testing_worker)  export REDIS_URL=redis://localhost:6379/3 ;;
  server_service)  export SERVER_ENCRYPTION_KEY="${SERVER_ENCRYPTION_KEY:-test-server-encryption-key-do-not-use-anywhere-else}" ;;
  server_worker)   export REDIS_URL=redis://localhost:6379/0 WORKER_BOT_TOKEN=dummy-test-token ;;
  secret_service)  export SECRET_ENCRYPTION_KEY="${SECRET_ENCRYPTION_KEY:-test-secret-encryption-key-do-not-use-anywhere-else}" \
                          SECRET_ENCRYPTION_KEY_VERSION=2 SWEEP_ENABLED=false ;;
  *) echo "unknown service ${service}" >&2; exit 2 ;;
esac

cd "${root}/${service}"
# Без явных путей — весь каталог tests/; с путями (tests/test_x.py::T) — только они.
targets=(tests/)
for a in "$@"; do case "$a" in tests/*) targets=(); break;; esac; done
exec pytest "${targets[@]}" -p no:cacheprovider "$@"
