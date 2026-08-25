-- Схема базы protopack, вынесена из psb_db_prep_stand12_olap.sh
-- MAC-метки на эти таблицы навешиваются отдельно, в new_balance/roles/web/db.py::setup_mac()

CREATE SCHEMA IF NOT EXISTS main;
CREATE SCHEMA IF NOT EXISTS other;

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

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
