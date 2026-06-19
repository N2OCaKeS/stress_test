#!/bin/bash
set -vx

# sudo apt install -y postgresql-common ca-certificates
# sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
# sudo apt update -y
# sudo apt install postgresql-16 -y


# ─── Конфигурация ────────────────────────────────────────────────────────────
PG_VERSION=16
PG_MAIN_CLUSTER=main
PG_MAIN_PORT=5432
PG_SETEST_CLUSTER=setest
PG_SETEST_PORT=5433
TABLESPACE_DEFAULT=/var/lib/postgresql/tablespace_default
MAIN_DIR=$(dirname "$(realpath "$0")")

# Авто-определение числа CPU
NCPUS=$(nproc)
PARALLEL_GATHER=$(( NCPUS / 2 < 1 ? 1 : NCPUS / 2 ))
PARALLEL_MAINT=$(( NCPUS / 4 < 4 ? NCPUS / 4 : 4 ))
PARALLEL_MAINT=$(( PARALLEL_MAINT < 1 ? 1 : PARALLEL_MAINT ))

# ─── Параметры памяти (128 GB RAM) ───────────────────────────────────────────
# shared_buffers     = 25%  RAM = 32 GB
# effective_cache    = 75%  RAM = 96 GB
# work_mem           = shared_buffers / (max_connections * 3) ≈ 54 MB
MEM_SHARED_BUFFERS="32768MB"
MEM_EFFECTIVE_CACHE="98304MB"
MEM_MAINTENANCE_WORK="2GB"
MEM_WORK="54613kB"
MEM_WAL_BUFFERS="16MB"
MAX_CONNECTIONS=800

# ─── Проверка прав суперпользователя ─────────────────────────────────────────
if [ "$UID" -ne "0" ]; then
    echo "Требуются права root"
    exit 1
fi


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                         Создание кластеров
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

test_cluster=$(pg_lsclusters | grep "$PG_VERSION $PG_SETEST_CLUSTER")
if [ "x$test_cluster" == "x" ]; then
    pg_createcluster $PG_VERSION $PG_SETEST_CLUSTER --port $PG_SETEST_PORT
fi

# Создание тестового табличного пространства
if [ ! -e "$TABLESPACE_DEFAULT" ]; then
    mkdir -p "$TABLESPACE_DEFAULT"
    chown postgres:postgres "$TABLESPACE_DEFAULT"
fi

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                       Настройка кластера setest
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

SETEST_CFG=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
SETEST_HBA=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
SETEST_CFG_NEW=${SETEST_CFG}.new

# pg_hint_plan + online_analyze + plantuner (установить расширения при необходимости)
sed -e "s/shared_preload_libraries.*/shared_preload_libraries = 'pg_stat_statements'/g" \
    "$SETEST_CFG" > "$SETEST_CFG_NEW"
mv "$SETEST_CFG_NEW" "$SETEST_CFG"

# Соединения
sed -i "s/.*max_connections.*/max_connections = ${MAX_CONNECTIONS}/g" "$SETEST_CFG"

# Память
sed -i "s/.*shared_buffers.*/shared_buffers = ${MEM_SHARED_BUFFERS}/g" "$SETEST_CFG"
sed -i "s/.*effective_cache_size.*/effective_cache_size = ${MEM_EFFECTIVE_CACHE}/g" "$SETEST_CFG"
sed -i "s/.*maintenance_work_mem.*/maintenance_work_mem = ${MEM_MAINTENANCE_WORK}/g" "$SETEST_CFG"
sed -i "s/.*work_mem.*/work_mem = ${MEM_WORK}/g" "$SETEST_CFG"
sed -i "s/.*wal_buffers.*/wal_buffers = ${MEM_WAL_BUFFERS}/g" "$SETEST_CFG"

# Планировщик
sed -i "s/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g" "$SETEST_CFG"
sed -i "s/.*default_statistics_target.*/default_statistics_target = 100/g" "$SETEST_CFG"
sed -i "s/.*random_page_cost.*/random_page_cost = 1.1/g" "$SETEST_CFG"
sed -i "s/.*effective_io_concurrency.*/effective_io_concurrency = 200/g" "$SETEST_CFG"

# WAL
sed -i "s/.*min_wal_size.*/min_wal_size = 1GB/g" "$SETEST_CFG"
sed -i "s/.*max_wal_size.*/max_wal_size = 4GB/g" "$SETEST_CFG"

# Параллелизм (авто по числу CPU)
sed -i "s/.*max_worker_processes.*/max_worker_processes = ${NCPUS}/g" "$SETEST_CFG"
sed -i "s/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = ${PARALLEL_GATHER}/g" "$SETEST_CFG"
sed -i "s/.*max_parallel_workers.*/max_parallel_workers = ${NCPUS}/g" "$SETEST_CFG"
sed -i "s/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = ${PARALLEL_MAINT}/g" "$SETEST_CFG"

chown postgres:postgres "$SETEST_CFG"

# pg_hba.conf — trust для локальных подключений (тестовый стенд)
sed -i -e 's/md5/trust/g' \
       -e 's/scram-sha-256/trust/g' \
       -e 's/\bpeer\b/trust/g' \
       "$SETEST_HBA"

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

# Перезапуск кластеров
pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER restart
pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER restart

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                           Создание БД
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
sudo apt install gawk -y

sql_script=$1

# Удаление main-кластера (default, порт 5432)
pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER stop
pg_dropcluster $PG_VERSION $PG_MAIN_CLUSTER --stop
rm -rf /etc/postgresql/$PG_VERSION/$PG_MAIN_CLUSTER

# Запуск SQL-скрипта на всех оставшихся кластерах
if [ -n "$sql_script" ]; then
    for port in $(pg_lsclusters -h | gawk '{print $3}'); do
        cp "$MAIN_DIR/sql/$sql_script" "/tmp/$sql_script"
        chmod 644 "/tmp/$sql_script"
        su -c "psql -p $port -f /tmp/$sql_script" postgres
#    sudo runuser -u postgres -- psql -p 5433 -f /tmp/psb_add_user.sql для AL smolensk vanila
        rm "/tmp/$sql_script"
    done
fi

# pgbench -i --scale=4000 --foreign-keys -h localhost -p 5433 -U postgres test_parsec