# docker-entrypoint-initdb.d/01-init-allta.sh
#!/usr/bin/env bash
set -euo pipefail

# 1) Создаём пользователя и саму БД
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
  -- роль, под которой будет работать приложение
  CREATE ROLE "${ALLTA_USER}"
    WITH LOGIN
         ENCRYPTED PASSWORD '${ALLTA_PASSWORD}';

  -- база, в которой будем хранить данные
  CREATE DATABASE "${ALLTA_DB}"
    WITH OWNER = "${POSTGRES_USER}"
         ENCODING = 'UTF8'
         LC_COLLATE = 'en_US.utf8'
         LC_CTYPE   = 'en_US.utf8'
         TEMPLATE   = template0;

  -- разрешаем allta всё внутри БД (CREATE CONNECTION, TEMPORARY, CREATE TYPE)
  GRANT ALL PRIVILEGES ON DATABASE "${ALLTA_DB}" TO "${ALLTA_USER}";
EOSQL

# 2) Внутри новой БД создаём свою схему и делаем её стандартной
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${ALLTA_DB}" <<-EOSQL
  -- создаём схему, владелец — our app-user
  CREATE SCHEMA "${ALLTA_SCHEMA}" AUTHORIZATION "${ALLTA_USER}";

  -- по умолчанию работать будем именно в этой схеме
  ALTER ROLE "${ALLTA_USER}" SET search_path TO "${ALLTA_SCHEMA}", public;

  -- удаляем ненужную уже БД postgres
  DROP DATABASE IF EXISTS postgres;
EOSQL

# 3) Удялаем ненужную схему public

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${ALLTA_DB}" <<-EOSQL
  -- удаляем ненужную схему public
  DROP SCHEMA IF EXISTS public CASCADE;
EOSQL

# # 4) Создаём таблицы
# psql -v ON_ERROR_STOP=1 --username "${ALLTA_USER}" --dbname "${ALLTA_DB}" \
#      -f "/docker-entrypoint-initdb.d/initdb.sql"

# # 5) Заполняем БД тестовыми данными

# psql -v ON_ERROR_STOP=1 --username "${ALLTA_USER}" --dbname "${ALLTA_DB}" \
#      -f "/docker-entrypoint-initdb.d/filling_db.sql"