#!/bin/bash

set -vx

export PG_MAIN_CLUSTER=main
export PG_MAIN_PORT=5432
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
  PG_VERSION=$(cat psb_conf.py | grep 'PG_VERSION_18 =' | awk '{print $3}')
else
  PG_VERSION=$(cat psb_conf.py | grep 'PG_VERSION =' | awk '{print $3}')
fi


STORAGE=`lsblk | awk 'NR==2' | awk '{print $1;}'`
MAIN_DIR=$(cat psb_conf.py | grep 'SCRIPT_DIR =' | awk '{print $3}' | tr -d "'")


PG_SETEST_CLUSTER=$(cat psb_conf.py | grep 'PG_SETEST_CLUSTER =' | awk '{print $3}' | tr -d "'")
PG_SETEST_PORT=$(cat psb_conf.py | grep 'PG_SETEST_PORT =' | awk '{print $3}')

TABLESPACE_DEFAULT=$(cat psb_conf.py | grep 'TABLESPACE_DEFAULT_PATH =' | awk '{print $3}' | tr -d "'")
TABLESPACE_MAC=$(cat psb_conf.py | grep 'TABLESPACE_MAC_PATH =' | awk '{print $3}' | tr -d "'")

EXT_REP=$(cat psb_conf.py | grep 'EXTREP' | tr -d 'EXTREP=' | tr -d "'")

# Проверка прав суперпользователя
if ["$UID" -ne "0"]; then
   exit
fi

## Выполнялось в старом тесте, сейчас такой необходимости нет, так как в 1.7 тесты на 11 версию, в 1.8 тесты на 15 версию
## Но оставлю на всякий случай, вдруг понадобится для 14 версии, которая есть в 1.7
if [[ "$PG_VERSION" -eq "14" ]]; then
  echo $EXT_REP >> /etc/apt/sources.list
  apt update
fi

# Установка PostgreSQL
if [[ $2 == "vanilla" ]]; then
  if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    #apt-get install libssl3 -y
    dpkg -i /home/u/postgresql_vanilla/16/lib*.deb
    dpkg -i /home/u/postgresql_vanilla/16/postgresql-client-common*.deb
    dpkg -i /home/u/postgresql_vanilla/16/postgresql-common*.deb
    DEBIAN_FRONTEND=noninteractive dpkg -i /home/u/postgresql_vanilla/16/postgresql-client-16*.deb
    DEBIAN_FRONTEND=noninteractive dpkg -i /home/u/postgresql_vanilla/16/postgresql-16*.deb
    PG_VERSION=16
  else
    dpkg -i /home/u/postgresql_vanilla/11/lib*.deb
    dpkg -i /home/u/postgresql_vanilla/11/postgresql-client-common*.deb
    dpkg -i /home/u/postgresql_vanilla/11/postgresql-common*.deb
    DEBIAN_FRONTEND=noninteractive dpkg -i /home/u/postgresql_vanilla/11/postgresql-client-11*.deb
    DEBIAN_FRONTEND=noninteractive dpkg -i /home/u/postgresql_vanilla/11/postgresql-11*.deb
  fi
else
  apt-get install -y postgresql-${PG_VERSION}
  apt-get install -y postgresql-se-test-${PG_VERSION}
fi


# Подготовка к выполнению тестов
cd /usr/share/postgresql/${PG_VERSION}/test/pgacext/

# Создание тестовых пользователей
useradd u_0_00 && usermac -m 0:0 -c 0:0 u_0_00
useradd u_1_01 && usermac -m 1:1 -c 1:1 u_1_01


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-# Создание кластеров #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Проверка существования и создание кластеров
test_cluster=`pg_lsclusters | grep "$PG_VERSION $PG_SETEST_CLUSTER"`

if [ "x$test_cluster" == "x" ]; then
	pg_createcluster $PG_VERSION $PG_SETEST_CLUSTER --port $PG_SETEST_PORT
fi

if [ ! -e $TABLESPACE_DEFAULT ]; then
	mkdir $TABLESPACE_DEFAULT
	chown postgres:postgres $TABLESPACE_DEFAULT
fi

# Создание тестового табличного пространства для работы с MAC
if [ ! -e $TABLESPACE_MAC ]
then
	mkdir $TABLESPACE_MAC
	chown postgres:postgres $TABLESPACE_MAC
	sudo chmod 770 $TABLESPACE_MAC
	sudo pdpl-file 3:0:3:ccnr $TABLESPACE_MAC
fi

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-# Настройка кластеров #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

### Настройка конфигурации основного сервера ###
hba=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
audit=/var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_audit.conf

hba_tst=/usr/share/postgresql/$PG_VERSION/test/pgacext/support/pg_hba.conf.tst
audit_tst=/usr/share/postgresql/$PG_VERSION/test/pgacext/support/pg_audit.conf.tst

setest_old_cfg=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
setest_new_cfg=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf.new


# pg_hint_plan
sed -e "s/shared_preload_libraries.*/shared_preload_libraries = 'online_analyze, plantuner, pg_hint_plan'/g" $setest_old_cfg > $setest_new_cfg
mv $setest_new_cfg $setest_old_cfg

# from tst in main conf
cp $hba_tst $hba
cp $audit_tst $audit

chown postgres.postgres /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/*

sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_connections.*/max_connections = 500/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*shared_buffers.*/shared_buffers = 64512MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_cache_size.*/effective_cache_size = 193536MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*maintenance_work_mem.*/maintenance_work_mem = 2GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*wal_buffers.*/wal_buffers = 16MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*default_statistics_target.*/default_statistics_target = 100/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*random_page_cost.*/random_page_cost = 1.1/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_io_concurrency.*/effective_io_concurrency = 200/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf


# Оставляю на всякий случай и 14 версию
if [[ "$PG_VERSION" -eq "14" ]]; then
  sed -i 's/.*work_mem.*/work_mem = 5242kB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
  sed -i 's/local *all *all *pear/local all all trust/' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
  sed -i 's/host *all *all * 127.0.0.1\/32 *scram-sha-256/host all all 127.0.0.1\/32 trust/' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
else
  sed -i 's/.*work_mem.*/work_mem = 82574kB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
fi


sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_worker_processes.*/max_worker_processes = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i -e 's/md5/trust/g' -e 's/scram-sha-256/trust/g' -e 's/peer/trust/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf


if [[ $2 == "audit_off" ]]; then
  sed -i "s/ac_audit_mode.*/ac_audit_mode = 'none'/g" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
fi


pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER restart
pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER restart


# Настройка необходимых прав пользователю postgres
pdpl-user -i 63 postgres
setfacl -m u:postgres:rx /etc/parsec/macdb
setfacl -m u:postgres:rx /etc/parsec/capdb
setfacl -d -m u:postgres:r /etc/parsec/macdb
setfacl -d -m u:postgres:r /etc/parsec/capdb
setfacl -R -m u:postgres:r /etc/parsec/macdb/*
setfacl -R -m u:postgres:r /etc/parsec/capdb/*


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#- Создать БД #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-


# delete main
pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER stop
pg_dropcluster $PG_VERSION $PG_MAIN_CLUSTER --stop
rm -rf /etc/postgresql/$PG_VERSION/$PG_MAIN_CLUSTER

#### OLAP
DB_NAME="protopack"
PG_VERSION="15"

# echo "--- 1. Установка PostgreSQL $PG_VERSION ---"
# sudo apt update
# sudo apt install -y postgresql-$PG_VERSION postgresql-client-$PG_VERSION

# echo "--- 2. Запуск сервиса ---"
# sudo systemctl start postgresql
# sudo systemctl enable postgresql

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


sudo wget -P /tmp ftp://10.177.103.205/upload/*

echo "--- 5. Импорт данных из файлов ---"
# Проверяем наличие файлов перед импортом
for FILE in build_info build_packages_new build_sourses; do
    if [ -f "/tmp/$FILE" ]; then
        echo "Импорт $FILE..."
        sudo -u postgres psql -d $DB_NAME -f "/tmp/$FILE"
    else
        echo "Предупреждение: Файл /tmp/$FILE не найден, пропуск."
    fi
done

echo "--- Готово! ---"
