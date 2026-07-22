#!/bin/bash
set -evx

# sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm
# sudo dnf -qy module disable postgresql
# sudo dnf install -y postgresql16-server


# ─── Конфигурация ────────────────────────────────────────────────────────────
PG_VERSION=16
PG_MAIN_PORT=5432
PG_SETEST_PORT=5433
PG_BIN=/usr/pgsql-${PG_VERSION}/bin
PGDATA_MAIN=/var/lib/pgsql/${PG_VERSION}/data
PGDATA_SETEST=/var/lib/pgsql/${PG_VERSION}/setest
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

# Инициализация setest если не существует
if [ ! -f "$PGDATA_SETEST/PG_VERSION" ]; then
    rm -rf "$PGDATA_SETEST"
    mkdir -p "$PGDATA_SETEST"
    chown postgres:postgres "$PGDATA_SETEST"
    su -c "$PG_BIN/initdb -D $PGDATA_SETEST" postgres
fi

# Создание тестового табличного пространства
if [ ! -e "$TABLESPACE_DEFAULT" ]; then
    mkdir -p "$TABLESPACE_DEFAULT"
    chown postgres:postgres "$TABLESPACE_DEFAULT"
fi

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                       Настройка кластера setest
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

SETEST_CFG=$PGDATA_SETEST/postgresql.conf
SETEST_HBA=$PGDATA_SETEST/pg_hba.conf
SETEST_CFG_NEW=${SETEST_CFG}.new

# shared_preload_libraries
sed -e "s/shared_preload_libraries.*/shared_preload_libraries = 'pg_stat_statements'/g" \
    "$SETEST_CFG" > "$SETEST_CFG_NEW"
mv "$SETEST_CFG_NEW" "$SETEST_CFG"

# Порт
sed -i "s/.*port.*/port = ${PG_SETEST_PORT}/g" "$SETEST_CFG"

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

# Создать systemd unit для setest инстанса
SETEST_UNIT=/etc/systemd/system/postgresql-${PG_VERSION}-setest.service
cat > "$SETEST_UNIT" <<UNIT
[Unit]
Description=PostgreSQL ${PG_VERSION} setest instance
After=network.target

[Service]
Type=forking
User=postgres
Group=postgres
Environment=PGDATA=${PGDATA_SETEST}
ExecStart=${PG_BIN}/pg_ctl start -D ${PGDATA_SETEST} -l ${PGDATA_SETEST}/pg.log
ExecStop=${PG_BIN}/pg_ctl stop -D ${PGDATA_SETEST} -m fast
ExecReload=${PG_BIN}/pg_ctl reload -D ${PGDATA_SETEST}
TimeoutSec=300

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload

# Запуск setest (main не нужен — он будет остановлен и удалён ниже)
systemctl restart postgresql-${PG_VERSION}-setest

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#                           Создание БД
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#

sql_script=$1

# Остановить и удалить main-инстанс (default, порт 5432)
systemctl stop postgresql-${PG_VERSION}
systemctl disable postgresql-${PG_VERSION}
rm -rf "$PGDATA_MAIN"

# Запуск SQL-скрипта на setest
if [ -n "$sql_script" ]; then
    cp "$MAIN_DIR/sql/$sql_script" "/tmp/$sql_script"
    chmod 644 "/tmp/$sql_script"
    su -s /bin/bash -c "$PG_BIN/psql -p $PG_SETEST_PORT -f /tmp/$sql_script" postgres
    rm "/tmp/$sql_script"
fi

/usr/pgsql-16/bin/pgbench -i --scale=4000 --foreign-keys -h localhost -p 5433 -U postgres test_parsec