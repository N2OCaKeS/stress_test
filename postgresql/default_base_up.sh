#!/bin/bash

################################
### Stand1 or stand2 use only
################################


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
#TABLESPACE_MAC=$(cat psb_conf.py | grep 'TABLESPACE_MAC_PATH =' | awk '{print $3}' | tr -d "'")
#EXT_REP=$(cat psb_conf.py | grep 'EXTREP' | tr -d 'EXTREP=' | tr -d "'")


if [ "$3" == "SDA" ]; then
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

if [ "$2" == "psql" ]; then
  apt-get install -y postgresql-${PG_VERSION}
  #apt-get install -y postgresql-se-test-${PG_VERSION}

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

  if [ "$4" != "4" ] && [ "$4" != "3" ]; then
    #sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 2000/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*shared_buffers\s*=.*/shared_buffers = 8GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*effective_cache_size\s*=.*/effective_cache_size = 24GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*maintenance_work_mem\s*=.*/maintenance_work_mem = 2GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*checkpoint_completion_target\s*=.*/checkpoint_completion_target = 0.9/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*wal_buffers\s*=.*/wal_buffers = 16MB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*default_statistics_target\s*=.*/default_statistics_target = 100/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*random_page_cost\s*=.*/random_page_cost = 1.1/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*effective_io_concurrency\s*=.*/effective_io_concurrency = 200/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*work_mem\s*=.*/work_mem = 10485kB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*min_wal_size\s*=.*/min_wal_size = 1GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_wal_size\s*=.*/max_wal_size = 4GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_worker_processes\s*=.*/max_worker_processes = 8/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_workers_per_gather\s*=.*/max_parallel_workers_per_gather = 4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_workers\s*=.*/max_parallel_workers = 8/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_maintenance_workers\s*=.*/max_parallel_maintenance_workers = 4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i -e 's/md5/trust/g' -e 's/scram-sha-256/trust/g' -e 's/peer/trust/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
  else
    #sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 2000/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*shared_buffers\s*=.*/shared_buffers = 32256MB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*effective_cache_size\s*=.*/effective_cache_size = 96768MB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*maintenance_work_mem\s*=.*/maintenance_work_mem = 2GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*checkpoint_completion_target\s*=.*/checkpoint_completion_target = 0.9/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*wal_buffers\s*=.*/wal_buffers = 16MB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*default_statistics_target\s*=.*/default_statistics_target = 100/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*random_page_cost\s*=.*/random_page_cost = 1.1/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*effective_io_concurrency\s*=.*/effective_io_concurrency = 200/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*work_mem\s*=.*/work_mem = 41287kB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*min_wal_size\s*=.*/min_wal_size = 1GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_wal_size\s*=.*/max_wal_size = 4GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_worker_processes\s*=.*/max_worker_processes = 32/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_workers_per_gather\s*=.*/max_parallel_workers_per_gather = 4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_workers\s*=.*/max_parallel_workers = 32/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_parallel_maintenance_workers\s*=.*/max_parallel_maintenance_workers = 4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i -e 's/md5/trust/g' -e 's/scram-sha-256/trust/g' -e 's/peer/trust/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
  fi
  #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-



  #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

  # Перезапуск кластеров
  pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER restart
  pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER restart


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
    cd -
    #cd - &> /dev/null
  done



  cat << EOF > start_test.sh
  #!/bin/bash
  clients="$1"
  t=30
  dir=test
  mkdir \$dir
  for c in \$clients; do
      echo "pgbench_\${c}_\${t}.txt"
      echo "start test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "\${dir}/pgbench_\${c}.txt"
      pgbench -h localhost -p 6000 -U postgres --random-seed=13 -T \$t -j \$c -c \$c test_parsec >> "\${dir}/pgbench_result.txt"
      echo "stop test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "\${dir}/pgbench_\${c}.txt"
  done
EOF

  cat start_test.sh

  pgbench -i -h localhost -p 6000 -U postgres -s 500 -F 100 test_parsec


  #pgbench -h localhost -p 6000 -U u_1 --random-seed=13 -T 30 -j 200 -c 200 test_parsec
fi


if [ "$2" == "tantor" ]; then
  if [ "$5" == "astra" ]; then
    wget --quiet -O - https://public.tantorlabs.ru/tantorlabs.ru.asc | apt-key add -
    #echo "deb [arch=amd64] https://tantor.astra:FVC6adbPafmB9bRZ@nexus.tantorlabs.ru/repository/astra-smolensk-1.7 smolensk main" > /etc/apt/sources.list.d/tantorlabs.list
    echo "deb [arch=amd64] https://nexus.tantorlabs.ru/repository/astra-smolensk-1.7 smolensk main" > /etc/apt/sources.list.d/tantorlabs.list
    echo "machine nexus.tantorlabs.ru login tantor.astra password FVC6adbPafmB9bRZ" > /etc/apt/auth.conf
    apt-get update
    apt-get install tantor-se-server-15 -y
  else
    dpkg -i tantor-se-server-15_15.2.2_amd64.deb
  fi

  chown postgres.postgres /var/lib/postgresql/tantor-se-15/data/*
  su -c "/opt/tantor/db/15/bin/initdb -D /var/lib/postgresql/tantor-se-15/data --no-instructions" postgres
  systemctl start tantor-se-server-15

  if [ "$4" == "4" ] && [ "$4" == "3" ]; then
    #sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
    sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 2000/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
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
    sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 2000/" /var/lib/postgresql/tantor-se-15/data/postgresql.conf
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
  

  cat << EOF > start_test.sh
  #!/bin/bash
  clients="$1"
  t=30
  dir=test
  mkdir \$dir
  for c in \$clients; do
      echo "pgbench_\${c}_\${t}.txt"
      echo "start test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "\${dir}/pgbench_\${c}.txt"
      /opt/tantor/db/15/bin/pgbench -h localhost -p 5432 -U postgres --random-seed=13 -T \$t -j \$c -c \$c test_parsec >> "\${dir}/pgbench_result.txt"
      echo "stop test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "\${dir}/pgbench_\${c}.txt"
  done
EOF

  cat start_test.sh

  /opt/tantor/db/15/bin/pgbench -i --scale=4000 --foreign-keys -h localhost -p 5432 -U postgres test_parsec
fi



