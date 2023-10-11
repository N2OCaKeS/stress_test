#!/bin/bash

set -vx

PG_MAIN_CLUSTER=main
PG_MAIN_PORT=5432
PG_VERSION=11
STORAGE=`lsblk | awk 'NR==2' | awk '{print $1;}'`
MAIN_DIR=$(cat psb_conf.py | grep 'SCRIPT_DIR =' | awk '{print $3}' | tr -d "'")
PG_SETEST_CLUSTER=$(cat psb_conf.py | grep 'PG_SETEST_CLUSTER =' | awk '{print $3}' | tr -d "'")
PG_SETEST_PORT=$(cat psb_conf.py | grep 'PG_SETEST_PORT =' | awk '{print $3}')
TABLESPACE_DEFAULT=$(cat psb_conf.py | grep 'TABLESPACE_DEFAULT_PATH =' | awk '{print $3}' | tr -d "'")
#TABLESPACE_MAC=$(cat psb_conf.py | grep 'TABLESPACE_MAC_PATH =' | awk '{print $3}' | tr -d "'")
#EXT_REP=$(cat psb_conf.py | grep 'EXTREP' | tr -d 'EXTREP=' | tr -d "'")


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
    mount /dev/${STORAGE}1 /var/lib/postgresql/11/
fi


apt-get install -y postgresql-${PG_VERSION}
#apt-get install -y postgresql-se-test-${PG_VERSION}


#Создаем пользователя
#sudo usermac -m 0:255 -c 0:0xFFFFFFFFFFFFFFFF postgres
#usercaps -m PARSEC_CAP_CHMAC:PARSEC_CAP_SETMAC postgres


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
sed -i 's/.*max_connections.*/max_connections = 200/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*shared_buffers.*/shared_buffers = 32256MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_cache_size.*/effective_cache_size = 96768MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*maintenance_work_mem.*/maintenance_work_mem = 2GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*wal_buffers.*/wal_buffers = 16MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*default_statistics_target.*/default_statistics_target = 100/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*random_page_cost.*/random_page_cost = 1.1/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_io_concurrency.*/effective_io_concurrency = 200/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf


if [[ "$PG_VERSION" -eq "14" ]]; then
  sed -i 's/.*work_mem.*/work_mem = 5242kB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
  sed -i 's/local *all *all *pear/local all all trust/' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
  sed -i 's/host *all *all * 127.0.0.1\/32 *scram-sha-256/host all all 127.0.0.1\/32 trust/' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
else
  sed -i 's/.*work_mem.*/work_mem = 41287kB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
fi

sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_worker_processes.*/max_worker_processes = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

if [[ $2 == "audit_off" ]]; then
  sed -i "s/ac_audit_mode.*/ac_audit_mode = 'none'/g" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
fi



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
  cp -r $MAIN_DIR/sql/$sql_script_add_user /tmp/$sql_script_add_user
  cp -r $MAIN_DIR/sql/$sql_script_set_mac /tmp/$sql_script_set_mac
  cd /tmp
  chmod 644 /tmp/$sql_script_add_user
  chmod 644 /tmp/$sql_script_set_mac
  su -c "psql -p $port -f /tmp/$sql_script_add_user" postgres
  su -c "psql -p $port -d test_parsec -f /tmp/$sql_script_set_mac" postgres
  #rm /tmp/$sql_script
  cd -
  #cd - &> /dev/null
done




#!/bin/bash
# clients="800 800 800 800 800 800 800 800 800 800"
# t=600
# dir=test
# mkdir /home/u/test
# for c in $clients; do
#     echo "pgbench_${c}_${t}.txt"
#     echo "start test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "${dir}/pgbench_${c}.txt"
#     pgbench -h localhost -p 5432 -U postgres --random-seed=13 -c $c -j $c -T $t test >> "${dir}/pgbench_${c}.txt"
#     echo "stop test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "${dir}/pgbench_${c}.txt"

# done

