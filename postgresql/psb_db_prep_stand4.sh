#!/bin/bash

set -vx

export PG_MAIN_CLUSTER=main
export PG_MAIN_PORT=5432
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
TABLESPACE_MAC=$(cat psb_conf.py | grep 'TABLESPACE_MAC_PATH =' | awk '{print $3}' | tr -d "'")
EXT_REP=$(cat psb_conf.py | grep 'EXTREP' | tr -d 'EXTREP=' | tr -d "'")

# Проверка прав суперпользователя
if ["$UID" -ne "0"]; then
   exit
fi

if [[ "$PG_VERSION" -eq "14" ]]; then
  echo $EXT_REP >> /etc/apt/sources.list
  apt update
fi

if [[ $2 == "vanilla" ]]; then
  if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    dpkg -i /home/u/postgresql_vanilla/16/lib*.deb
    dpkg -i /home/u/postgresql_vanilla/16/postgresql-client-common*.deb
    dpkg -i /home/u/postgresql_vanilla/16/postgresql-common*.deb
    DEBIAN_FRONTEND=noninteractive dpkg -i /home/u/postgresql_vanilla/16/postgresql-client-16*.deb
    DEBIAN_FRONTEND=noninteractive dpkg -i /home/u/postgresql_vanilla/16/postgresql-16*.deb
  else
    dpkg -i /home/u/postgresql_vanilla/11/lib*.deb
    dpkg -i /home/u/postgresql_vanilla/11/postgresql-client-common*.deb
    dpkg -i /home/u/postgresql_vanilla/11/postgresql-common*.deb
    DEBIAN_FRONTEND=noninteractive dpkg -i /home/u/postgresql_vanilla/11/postgresql-client-11*.deb
    DEBIAN_FRONTEND=noninteractive dpkg -i /home/u/postgresql_vanilla/11/postgresql-11*.deb
  fi
else
  apt-get install -y postgresql-${PG_VERSION}
  apt-get install -y postgresql-se-test-${PG_VERSION}
fi

# Подготовка к выполнению тестов
cd /usr/share/postgresql/${PG_VERSION}/test/pgacext/

# Создание тестовых пользователей
useradd u_0_00 && usermac -m 0:0 -c 0:0 u_0_00
useradd u_1_01 && usermac -m 1:1 -c 1:1 u_1_01

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-# Создание кластеров #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Проверка существования и создание кластеров
test_cluster=`pg_lsclusters | grep "$PG_VERSION $PG_SETEST_CLUSTER"`
# foreign_cluster=`pg_lsclusters | grep "$PG_VERSION $PG_SEFOREIGN_CLUSTER"`
# file_cluster=`pg_lsclusters | grep "$PG_VERSION $PG_FILES_CLUSTER"`

if [ "x$test_cluster" == "x" ]; then
	pg_createcluster $PG_VERSION $PG_SETEST_CLUSTER --port $PG_SETEST_PORT
fi
# if [ "x$foreign_cluster" == "x" ]; then
# 	pg_createcluster $PG_VERSION $PG_SEFOREIGN_CLUSTER --port $PG_SEFOREIGN_PORT
# fi
# if [ "x$file_cluster" == "x" ]; then
# 	pg_createcluster $PG_VERSION $PG_FILES_CLUSTER -D /$PG_FILES_CLUSTER --port $PG_FILES_PORT
# fi

# Создание тестового табличного пространства в ФС
if [ ! -e $TABLESPACE_DEFAULT ]; then
	mkdir $TABLESPACE_DEFAULT
	chown postgres:postgres $TABLESPACE_DEFAULT
fi

# Создание тестового табличного пространства для работы с MAC
if [ ! -e $TABLESPACE_MAC ]
then
	mkdir $TABLESPACE_MAC
	chown postgres:postgres $TABLESPACE_MAC
	sudo chmod 770 $TABLESPACE_MAC
	sudo pdpl-file 3:0:3:ccnr $TABLESPACE_MAC
fi

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-# Настройка кластеров #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
### Настройка конфигурации основного сервера ###
hba=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
audit=/var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_audit.conf

hba_tst=/usr/share/postgresql/$PG_VERSION/test/pgacext/support/pg_hba.conf.tst
audit_tst=/usr/share/postgresql/$PG_VERSION/test/pgacext/support/pg_audit.conf.tst

setest_old_cfg=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
setest_new_cfg=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf.new

# pg_hint_plan
sed -e "s/shared_preload_libraries.*/shared_preload_libraries = 'online_analyze, plantuner, pg_hint_plan'/g" $setest_old_cfg > $setest_new_cfg
mv $setest_new_cfg $setest_old_cfg

# from tst in main conf
cp $hba_tst $hba
cp $audit_tst $audit

chown postgres.postgres /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/*

sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_connections.*/max_connections = 200/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*shared_buffers.*/shared_buffers = 64512MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_cache_size.*/effective_cache_size = 193536MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
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
  sed -i 's/.*work_mem.*/work_mem = 82574kB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
fi

sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_worker_processes.*/max_worker_processes = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/md5/trust/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

if [[ $2 == "audit_off" ]]; then
  sed -i "s/ac_audit_mode.*/ac_audit_mode = 'none'/g" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
fi

# ### Настройка конфигурации внешнего сервера ###
# old_cfg=/etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER/postgresql.conf
# new_cfg=/etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER/postgresql.conf.new
#
# sed -e 's/ac_ignore_socket_maclabel.*/ac_ignore_socket_maclabel = false/g' $old_cfg > $new_cfg
# mv $new_cfg $old_cfg
#
# foreign_hba=/etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER/pg_hba.conf
# foreign_hba_tst=/usr/share/postgresql/$PG_VERSION/test/pgacext/support/pg_hba_foreign.conf.tst
#
# cp $foreign_hba_tst $foreign_hba
#
# chown postgres.postgres /etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER/*

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

### Настройка конфигурации дополнительного сервера с метками на файлах

# from tst in main conf
# cp $hba_tst /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/pg_hba.conf
#
# sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*max_connections.*/max_connections = 200/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*shared_buffers.*/shared_buffers = 8GB/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*effective_cache_size.*/effective_cache_size = 24GB/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*maintenance_work_mem.*/maintenance_work_mem = 2GB/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*wal_buffers.*/wal_buffers = 16MB/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*default_statistics_target.*/default_statistics_target = 100/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*random_page_cost.*/random_page_cost = 1.1/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*effective_io_concurrency.*/effective_io_concurrency = 200/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*work_mem.*/work_mem = 10485kB/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*max_worker_processes.*/max_worker_processes = 8/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 8/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf
# sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER/postgresql.conf


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Перезапуск кластеров
pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER restart
pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER restart
# pg_ctlcluster $PG_VERSION $PG_SEFOREIGN_CLUSTER restart
# pg_ctlcluster $PG_VERSION $PG_FILES_CLUSTER restart

# Настройка необходимых прав пользователю postgres
setfacl -m u:postgres:rx /etc/parsec/macdb
setfacl -m u:postgres:rx /etc/parsec/capdb
setfacl -d -m u:postgres:r /etc/parsec/macdb
setfacl -d -m u:postgres:r /etc/parsec/capdb
setfacl -R -m u:postgres:r /etc/parsec/macdb/*
setfacl -R -m u:postgres:r /etc/parsec/capdb/*

#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#- Создать БД #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Создать базу
sql_script=$1

# delete main
pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER stop
pg_dropcluster $PG_VERSION $PG_MAIN_CLUSTER --stop
rm -rf /etc/postgresql/$PG_VERSION/$PG_MAIN_CLUSTER

# подключить диск
if [ "$2" == "SDA" ] || [ "$3" == "SDA" ]; then
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

for port in $(pg_lsclusters -h | gawk '{print $3}');
do
  cp $MAIN_DIR/sql/$sql_script /tmp/$sql_script
  cd /tmp
  chmod 644 /tmp/$sql_script
  su -c "psql -p $port -f /tmp/$sql_script" postgres
  rm /tmp/$sql_script
  cd - &> /dev/null
done
