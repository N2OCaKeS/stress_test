#!/bin/bash

set -e

DB_NAME="protopack"
PG_VERSION="15"

echo "--- 1. Установка PostgreSQL $PG_VERSION ---"
sudo apt update
sudo apt install -y postgresql-$PG_VERSION postgresql-client-$PG_VERSION

echo "--- 2. Запуск сервиса ---"
sudo systemctl start postgresql
sudo systemctl enable postgresql

echo "--- 3. Создание базы данных ---"

sudo -u postgres createdb --encoding=UTF8 --locale=C --template=template0 $DB_NAME || echo "База уже существует"

echo "--- 4. Создание схем и таблиц ---"
sudo -u postgres psql -d $DB_NAME <<EOF
CREATE SCHEMA IF NOT EXISTS main;
CREATE SCHEMA IF NOT EXISTS other;

CREATE TABLE IF NOT EXISTS main.build_info (
    build text PRIMARY KEY,
    build_type text[],
    create_utc timestamp with time zone,
    repo jsonb,
    rel text,
    mount_point jsonb,
    count_packages integer,
    packages_sync boolean DEFAULT false
) WITH (OIDS = FALSE);

CREATE TABLE IF NOT EXISTS main.build_packages (
    build text,
    repository text,
    component text,
    "binary" text,
    binary_version text,
    source text,
    source_version text,
    sha256 text,
    binary_info text,
    licence text,
    files text,
    binary_info_json jsonb,
    depends jsonb,
    section text,
    task text[],
    fb text[],
    fn text[],
    department text,
    sdk text,
    responsible text,
    create_utc timestamp with time zone,
    jira_component text
) WITH (OIDS = FALSE);

CREATE TABLE IF NOT EXISTS main.build_sources (
    build text,
    source text,
    source_version text,
    description text,
    repository text,
    binary_packages text[],
    fb text[],
    department text,
    sdk text,
    jira_component text
) WITH (OIDS = FALSE);
EOF


echo "--- 5. Импорт данных из файлов ---"
# Проверяем наличие файлов перед импортом
for FILE in build_info build_packages_new build_sources; do
    if [ -f "/tmp/$FILE" ]; then
        echo "Импорт $FILE..."
        sudo -u postgres psql -d $DB_NAME -f "/tmp/$FILE"
    else
        echo "Предупреждение: Файл /tmp/$FILE не найден, пропуск."
    fi
done

echo "--- Готово! ---"