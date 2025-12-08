#!/bin/bash

set -vx

PG_MAIN_CLUSTER=main
PG_MAIN_PORT=5432
PG_VERSION=$1
SCRIPT_DIR=$2
PG_SETEST_CLUSTER='setest_cl'
PG_SETEST_PORT=$3




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
hba=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
audit=/var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_audit.conf
chown postgres.postgres /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/*

#sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_connections.*/max_connections = 5000/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*shared_buffers.*/shared_buffers = '"$4"'/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_cache_size.*/effective_cache_size = '"$5"'/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*maintenance_work_mem.*/maintenance_work_mem = 2GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*wal_buffers.*/wal_buffers = 16MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*default_statistics_target.*/default_statistics_target = 100/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*random_page_cost.*/random_page_cost = 1.1/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_io_concurrency.*/effective_io_concurrency = 200/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*work_mem.*/work_mem = '"$6"'/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_worker_processes.*/max_worker_processes = '"$7"'/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i -e 's/md5/trust/g' -e 's/scram-sha-256/trust/g' -e 's/peer/trust/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
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

# Удалить main
pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER stop
pg_dropcluster $PG_VERSION $PG_MAIN_CLUSTER --stop
rm -rf /etc/postgresql/$PG_VERSION/$PG_MAIN_CLUSTER


for port in $(pg_lsclusters -h | gawk '{print $3}');
do
  echo "Выполняется настройка базы данных (add_user, parsec)"
  cp -r $SCRIPT_DIR/srv/sql/$sql_script_add_user /tmp/$sql_script_add_user
  cp -r $SCRIPT_DIR/srv/sql/$sql_script_set_mac /tmp/$sql_script_set_mac
  cd /tmp
  chmod 644 /tmp/$sql_script_add_user
  chmod 644 /tmp/$sql_script_set_mac
  sudo -u postgres psql -p $port -f /tmp/$sql_script_add_user
  sudo -u postgres psql -p $port -d test_parsec -f /tmp/$sql_script_set_mac
  cd -
  #cd - &> /dev/null
done

