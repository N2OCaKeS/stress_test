#!/bin/bash

set -vx

PG_MAIN_CLUSTER=main
PG_MAIN_PORT=5432
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
MAX_CONNECTIONS=850



if [ "$1" == "SDA" ]; then
  # подключить диск
  lsblk | grep "${STORAGE}"
  if [ $? -eq 0 ]; then
      lsblk | grep "${STORAGE}"
      if [ $? -eq 0 ]; then
          umount /var/lib/postgresql/11/
          parted -s /dev/${STORAGE} select && parted -s /dev/${STORAGE} rm 1
      fi
      parted -s /dev/${STORAGE} mklabel msdos mkpart primary xfs 0% 100%
      mkfs -t xfs -f /dev/${STORAGE}1
      mkdir /var/lib/postgresql
      mount /dev/${STORAGE}1 /var/lib/postgresql
  fi
fi


apt-get install -y postgresql-${PG_VERSION}

#Создаем пользователя
useradd u_1 
pdpl-user -i 63 u_1
pdpl-user -i 63 postgres
pdpl-user -l 0:255 -c 0:0xFFFFFFFFFFFFFFFF u_1
usercaps -m PARSEC_CAP_CHMAC:PARSEC_CAP_SETMAC u_1


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-# Создание кластеров #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Проверка существования и создание кластеров
test_cluster=`pg_lsclusters | grep "$PG_VERSION $PG_SETEST_CLUSTER"`
if [ "x$test_cluster" == "x" ]; then
	pg_createcluster $PG_VERSION $PG_SETEST_CLUSTER --port $PG_SETEST_PORT
fi


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-# Настройка кластеров #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
### Настройка конфигурации основного сервера ###
chown postgres:postgres /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/*

#sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf

SETEST_CFG=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
SETEST_HBA=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
SETEST_CFG_NEW=${SETEST_CFG}.new

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
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-



#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Перезапуск кластеров
pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER restart
pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER restart

# Настройка необходимых прав пользователю postgres
usermod -a -G shadow postgres
setfacl -d -m u:postgres:r /etc/parsec/macdb
setfacl -R -m u:postgres:r /etc/parsec/macdb
setfacl -m u:postgres:rx /etc/parsec/macdb
setfacl -d -m u:postgres:r /etc/parsec/capdb
setfacl -R -m u:postgres:r /etc/parsec/capdb
setfacl -m u:postgres:rx /etc/parsec/capdb


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#- Создать БД #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Создать базу
sql_script_add_user=psb_add_user.sql
sql_script_set_mac=psb_parsec.sql

# delete main
pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER stop
pg_dropcluster $PG_VERSION $PG_MAIN_CLUSTER --stop
rm -rf /etc/postgresql/$PG_VERSION/$PG_MAIN_CLUSTER


for port in $(pg_lsclusters -h | gawk '{print $3}');
do
  echo "Выполняется настройка базы данных (add_user, parsec)"
  cp -r $MAIN_DIR/sql/$sql_script_add_user /tmp/$sql_script_add_user
  cp -r $MAIN_DIR/sql/$sql_script_set_mac /tmp/$sql_script_set_mac
  cd /tmp
  chmod 644 /tmp/$sql_script_add_user
  chmod 644 /tmp/$sql_script_set_mac
  sudo -u postgres psql -p $port -f /tmp/$sql_script_add_user
  sudo -u postgres psql -p $port -d test_parsec -f /tmp/$sql_script_set_mac
  #su -c "psql -p $port -f /tmp/$sql_script_add_user" postgres
  #su -c "psql -p $port -d test_parsec -f /tmp/$sql_script_set_mac" postgres
  cd -
  #cd - &> /dev/null
done

##### pgbench -i -h localhost --macs=fixed -p 6000 -U postgres --scale=4000 --foreign-keys test_parsec