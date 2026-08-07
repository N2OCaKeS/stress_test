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
    sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 500/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*shared_buffers\s*=.*/shared_buffers = 32256MB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*effective_cache_size\s*=.*/effective_cache_size = 96768MB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*maintenance_work_mem\s*=.*/maintenance_work_mem = 2GB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*checkpoint_completion_target\s*=.*/checkpoint_completion_target = 0.9/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*wal_buffers\s*=.*/wal_buffers = 16MB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*default_statistics_target\s*=.*/default_statistics_target = 100/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*random_page_cost\s*=.*/random_page_cost = 1.1/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*effective_io_concurrency\s*=.*/effective_io_concurrency = 200/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*min_wal_size\s*=.*/min_wal_size = 1GB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_wal_size\s*=.*/max_wal_size = 4GB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_worker_processes\s*=.*/max_worker_processes = 32/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_workers_per_gather\s*=.*/max_parallel_workers_per_gather = 4/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_workers\s*=.*/max_parallel_workers = 32/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_maintenance_workers\s*=.*/max_parallel_maintenance_workers = 4/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/md5/trust/g' /var/lib/postgresql/tantor-se-15/data/pg_hba.conf
else
    sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 500/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*shared_buffers\s*=.*/shared_buffers = 8GB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*effective_cache_size\s*=.*/effective_cache_size = 24GB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*maintenance_work_mem\s*=.*/maintenance_work_mem = 2GB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*checkpoint_completion_target\s*=.*/checkpoint_completion_target = 0.9/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*wal_buffers\s*=.*/wal_buffers = 16MB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*default_statistics_target\s*=.*/default_statistics_target = 100/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*random_page_cost\s*=.*/random_page_cost = 1.1/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*min_wal_size\s*=.*/min_wal_size = 1GB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_wal_size\s*=.*/max_wal_size = 4GB/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_worker_processes\s*=.*/max_worker_processes = 8/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_workers_per_gather\s*=.*/max_parallel_workers_per_gather = 4/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_workers\s*=.*/max_parallel_workers = 8/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_maintenance_workers\s*=.*/max_parallel_maintenance_workers = 4/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
    sed -i 's/md5/trust/g' /var/lib/postgresql/tantor-se-15/data/pg_hba.conf
fi

systemctl restart tantor-se-server-15

sql_script_add_user=psb_add_user.sql
cp -r $MAIN_DIR/sql/$sql_script_add_user /tmp/$sql_script_add_user
cd /tmp
chmod 644 /tmp/$sql_script_add_user
su -c "/opt/tantor/db/15/bin/psql -p 5432 -f /tmp/$sql_script_add_user" postgres
cd -