#!/bin/bash
set -vx

# ALT SP Workstation 10.2 (c10f2)
# Перед запуском установить пакеты:
#   apt-get install -y postgresql15-server postgresql15-contrib gawk

# ─── Конфигурация ────────────────────────────────────────────────────────────
PG_VERSION=16
PG_SETEST_PORT=5433
PG_SETEST_DATA=/var/lib/pgsql/setest
TABLESPACE_DEFAULT=/var/lib/pgsql/tablespace_default
MAIN_DIR=$(dirname "$(realpath "$0")")

# Авто-определение числа CPU
NCPUS=$(nproc)
PARALLEL_GATHER=$(( NCPUS / 2 < 1 ? 1 : NCPUS / 2 ))
PARALLEL_MAINT=$(( NCPUS / 4 < 4 ? NCPUS / 4 : 4 ))
PARALLEL_MAINT=$(( PARALLEL_MAINT < 1 ? 1 : PARALLEL_MAINT ))

# ─── Параметры памяти (128 GB RAM) ───────────────────────────────────────────
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

# ─── Определение пути к binaries PostgreSQL ──────────────────────────────────
PG_BIN=$(su -c "pg_ctl --version" postgres 2>/dev/null | head -1 | grep -o '' || true)
for d in /usr/lib/postgresql/$PG_VERSION/bin /usr/pgsql-$PG_VERSION/bin /usr/bin; do
    if [ -x "$d/pg_ctl" ]; then
        PG_BIN=$d
        break
    fi
done
if [ -z "$PG_BIN" ]; then
    echo "Не найден pg_ctl. Проверьте установку postgresql${PG_VERSION}-server"
    exit 1
fi
INITDB=$PG_BIN/initdb
PG_CTL=$PG_BIN/pg_ctl

apt-get install -y gawk

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                  Остановка стандартного сервиса postgresql
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

systemctl stop postgresql 2>/dev/null || true
systemctl disable postgresql 2>/dev/null || true

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                         Создание кластера setest
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

if [ ! -f "$PG_SETEST_DATA/PG_VERSION" ]; then
    rm -rf "$PG_SETEST_DATA"
    mkdir -p "$PG_SETEST_DATA"
    chown postgres:postgres "$PG_SETEST_DATA"
    su -s /bin/bash -c "$INITDB -D $PG_SETEST_DATA --locale=ru_RU.UTF-8 --encoding=UTF8" postgres
fi

# Создание тестового табличного пространства
if [ ! -e "$TABLESPACE_DEFAULT" ]; then
    mkdir -p "$TABLESPACE_DEFAULT"
    chown postgres:postgres "$TABLESPACE_DEFAULT"
fi

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                       Настройка кластера setest
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

SETEST_CFG=$PG_SETEST_DATA/postgresql.conf
SETEST_HBA=$PG_SETEST_DATA/pg_hba.conf
SETEST_CFG_NEW=${SETEST_CFG}.new

sed -e "s/shared_preload_libraries.*/shared_preload_libraries = 'pg_stat_statements'/g" \
    "$SETEST_CFG" > "$SETEST_CFG_NEW"
mv "$SETEST_CFG_NEW" "$SETEST_CFG"

# Порт (на ALT нет pg_createcluster — прописываем вручную)
sed -i "s/^#*port.*/port = ${PG_SETEST_PORT}/g" "$SETEST_CFG"

# Соединения
sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = ${MAX_CONNECTIONS}/" "$SETEST_CFG"

# Память
sed -i "s/^#\?\s*shared_buffers\s*=.*/shared_buffers = ${MEM_SHARED_BUFFERS}/" "$SETEST_CFG"
sed -i "s/^#\?\s*effective_cache_size\s*=.*/effective_cache_size = ${MEM_EFFECTIVE_CACHE}/" "$SETEST_CFG"
sed -i "s/^#\?\s*maintenance_work_mem\s*=.*/maintenance_work_mem = ${MEM_MAINTENANCE_WORK}/" "$SETEST_CFG"
sed -i "s/^#\?\s*work_mem\s*=.*/work_mem = ${MEM_WORK}/" "$SETEST_CFG"
sed -i "s/^#\?\s*wal_buffers\s*=.*/wal_buffers = ${MEM_WAL_BUFFERS}/" "$SETEST_CFG"

# Планировщик
sed -i "s/^#\?\s*checkpoint_completion_target\s*=.*/checkpoint_completion_target = 0.9/" "$SETEST_CFG"
sed -i "s/^#\?\s*default_statistics_target\s*=.*/default_statistics_target = 100/" "$SETEST_CFG"
sed -i "s/^#\?\s*random_page_cost\s*=.*/random_page_cost = 1.1/" "$SETEST_CFG"
sed -i "s/^#\?\s*effective_io_concurrency\s*=.*/effective_io_concurrency = 200/" "$SETEST_CFG"

# WAL
sed -i "s/^#\?\s*min_wal_size\s*=.*/min_wal_size = 1GB/" "$SETEST_CFG"
sed -i "s/^#\?\s*max_wal_size\s*=.*/max_wal_size = 4GB/" "$SETEST_CFG"

# Параллелизм
sed -i "s/^#\?\s*max_worker_processes\s*=.*/max_worker_processes = ${NCPUS}/" "$SETEST_CFG"
sed -i "s/^#\?\s*max_parallel_workers_per_gather\s*=.*/max_parallel_workers_per_gather = ${PARALLEL_GATHER}/" "$SETEST_CFG"
sed -i "s/^#\?\s*max_parallel_workers\s*=.*/max_parallel_workers = ${NCPUS}/" "$SETEST_CFG"
sed -i "s/^#\?\s*max_parallel_maintenance_workers\s*=.*/max_parallel_maintenance_workers = ${PARALLEL_MAINT}/" "$SETEST_CFG"

chown postgres:postgres "$SETEST_CFG"

# pg_hba.conf — trust для локальных подключений (тестовый стенд)
sed -i -e 's/md5/trust/g' \
       -e 's/scram-sha-256/trust/g' \
       -e 's/\bpeer\b/trust/g' \
       "$SETEST_HBA"

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                        Запуск кластера setest
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

# Остановить кластер, если уже запущен
su -s /bin/bash -c "$PG_CTL -D $PG_SETEST_DATA stop -m fast" postgres 2>/dev/null || true

su -s /bin/bash -c "$PG_CTL -D $PG_SETEST_DATA -l /var/lib/pgsql/setest.log start" postgres

# Ждём готовности
for i in $(seq 1 15); do
    su -s /bin/bash -c "psql -p $PG_SETEST_PORT -U postgres -c 'SELECT 1'" postgres 2>/dev/null && break
    sleep 1
done

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                           Создание БД / SQL-скрипт
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

sql_script=$1

if [ -n "$sql_script" ]; then
    cp "$MAIN_DIR/sql/$sql_script" "/tmp/$sql_script"
    chmod 644 "/tmp/$sql_script"
    su -s /bin/bash -c "psql -p $PG_SETEST_PORT -f /tmp/$sql_script" postgres
    rm "/tmp/$sql_script"
fi

# pgbench -i --scale=4000 --foreign-keys -h localhost -p $PG_SETEST_PORT -U postgres test_parsec
