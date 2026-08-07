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
usermac -m 0:255 -c 0:0xFFFFFFFFFFFFFFFF u_1
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
sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 200/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*shared_buffers\s*=.*/shared_buffers = 8GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*effective_cache_size\s*=.*/effective_cache_size = 24GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*maintenance_work_mem\s*=.*/maintenance_work_mem = 2GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*checkpoint_completion_target\s*=.*/checkpoint_completion_target = 0.9/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*wal_buffers\s*=.*/wal_buffers = 16MB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*default_statistics_target\s*=.*/default_statistics_target = 100/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*random_page_cost\s*=.*/random_page_cost = 1.1/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*effective_io_concurrency\s*=.*/effective_io_concurrency = 200/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf


if [[ "$PG_VERSION" -eq "14" ]]; then
  sed -i "s/^#\?\s*work_mem\s*=.*/work_mem = 5242kB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
  sed -i 's/local *all *all *pear/local all all trust/' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
  sed -i 's/host *all *all * 127.0.0.1\/32 *scram-sha-256/host all all 127.0.0.1\/32 trust/' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
else
  sed -i "s/^#\?\s*work_mem\s*=.*/work_mem = 10485kB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
fi

sed -i "s/^#\?\s*min_wal_size\s*=.*/min_wal_size = 1GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_wal_size\s*=.*/max_wal_size = 4GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_worker_processes\s*=.*/max_worker_processes = 8/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_parallel_workers_per_gather\s*=.*/max_parallel_workers_per_gather = 4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_parallel_workers\s*=.*/max_parallel_workers = 8/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_parallel_maintenance_workers\s*=.*/max_parallel_maintenance_workers = 4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/md5/trust/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
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
  cp -r $MAIN_DIR/sql/$sql_script_add_user /tmp/$sql_script_add_user
  cp -r $MAIN_DIR/sql/$sql_script_set_mac /tmp/$sql_script_set_mac
  cd /tmp
  chmod 644 /tmp/$sql_script_add_user
  chmod 644 /tmp/$sql_script_set_mac
  su -c "psql -p $port -f /tmp/$sql_script_add_user" postgres
  su -c "psql -p $port -d test_parsec -f /tmp/$sql_script_set_mac" postgres
  cd -
  #cd - &> /dev/null
done




#astra-modeswitch set 2 && astra-mac-control enable && astra-mic-control enable && reboot

cp /home/u/git/stress_test/postgresql/pgbench/pgbench /usr/bin/pgbench
cp /home/u/git/stress_test/postgresql/pgbench/pgbench /bin/pgbench

#pgbench -i -h localhost --macs -p 6000 -U postgres -s 500 -F 100 test_parsec
#pgbench -h localhost --macs -p 6000 -U u_1 --random-seed=13 -T 30 -j 200 -c 200 test_parsec

