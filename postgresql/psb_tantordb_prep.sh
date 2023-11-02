#!/bin/bash

set -vx

export PG_MAIN_CLUSTER=main
export PG_MAIN_PORT=5432
PG_VERSION=$(cat psb_conf.py | grep 'PG_VERSION =' | awk '{print $3}')
STORAGE=`lsblk | awk 'NR==2' | awk '{print $1;}'`
MAIN_DIR=$(cat psb_conf.py | grep 'SCRIPT_DIR =' | awk '{print $3}' | tr -d "'")
PG_SETEST_CLUSTER=$(cat psb_conf.py | grep 'PG_SETEST_CLUSTER =' | awk '{print $3}' | tr -d "'")
PG_SETEST_PORT=$(cat psb_conf.py | grep 'PG_SETEST_PORT =' | awk '{print $3}')
TABLESPACE_DEFAULT=$(cat psb_conf.py | grep 'TABLESPACE_DEFAULT_PATH =' | awk '{print $3}' | tr -d "'")
TABLESPACE_MAC=$(cat psb_conf.py | grep 'TABLESPACE_MAC_PATH =' | awk '{print $3}' | tr -d "'")
EXT_REP=$(cat psb_conf.py | grep 'EXTREP' | tr -d 'EXTREP=' | tr -d "'")


# подключить диск
if [ "$3" == "SDA" ]; then
  lsblk | grep "${STORAGE}"
  if [ $? -eq 0 ]; then
      lsblk | grep "${STORAGE}"
      if [ $? -eq 0 ]; then
          umount /var/lib/postgresql/11/
          parted -s /dev/${STORAGE} select && parted -s /dev/${STORAGE} rm 1
      fi
      parted -s /dev/${STORAGE} mklabel msdos mkpart primary xfs 0% 100%
      mkfs -t xfs -f /dev/${STORAGE}1
      mount /dev/${STORAGE}1 /var/lib/postgresql/11/
  fi
fi

if [ "$1" == "tantor" ]; then
    wget --quiet -O - https://public.tantorlabs.ru/tantorlabs.ru.asc | apt-key add -
    echo "deb [arch=amd64] https://nexus.tantorlabs.ru/repository/astra-smolensk-1.7 smolensk main" > /etc/apt/sources.list.d/tantorlabs.list
    echo "machine nexus.tantorlabs.ru login tantor.astra password FVC6adbPafmB9bRZ" > /etc/apt/auth.conf
    apt-get update
    apt-get install tantor-se-server-15 -y
fi

chown postgres.postgres /var/lib/postgresql/tantor-se-15/data/*
su -c "/opt/tantor/db/15/bin/initdb -D /var/lib/postgresql/tantor-se-15/data --no-instructions" postgres
systemctl start tantor-se-server-15

if [ "$2" == "4" ]; then
    #sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i 's/.*max_connections.*/max_connections = 500/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*shared_buffers.*/shared_buffers = 32256MB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*effective_cache_size.*/effective_cache_size = 96768MB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*maintenance_work_mem.*/maintenance_work_mem = 2GB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*wal_buffers.*/wal_buffers = 16MB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*default_statistics_target.*/default_statistics_target = 100/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*random_page_cost.*/random_page_cost = 1.1/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*effective_io_concurrency.*/effective_io_concurrency = 200/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_worker_processes.*/max_worker_processes = 32/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 32/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/md5/trust/g' /var/lib/postgresql/tantor-se-15/data/pg_hba.conf
else
    sed -i 's/.*max_connections.*/max_connections = 500/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*shared_buffers.*/shared_buffers = 8GB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*effective_cache_size.*/effective_cache_size = 24GB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*maintenance_work_mem.*/maintenance_work_mem = 2GB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*wal_buffers.*/wal_buffers = 16MB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*default_statistics_target.*/default_statistics_target = 100/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*random_page_cost.*/random_page_cost = 1.1/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_worker_processes.*/max_worker_processes = 8/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 8/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/md5/trust/g' /var/lib/postgresql/tantor-se-15/data/pg_hba.conf
fi

systemctl restart tantor-se-server-15

sql_script_add_user=psb_add_user.sql
cp -r $MAIN_DIR/sql/$sql_script_add_user /tmp/$sql_script_add_user
cd /tmp
chmod 644 /tmp/$sql_script_add_user
su -c "/opt/tantor/db/15/bin/psql -p 5432 -f /tmp/$sql_script_add_user" postgres
cd -