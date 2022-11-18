#!/usr/bin/env bash
set -vx
export PG_VERSION=$(cat psb_conf.py | grep 'PG_VERSION =' | awk '{print $3}')

export PG_MAIN_CLUSTER=main
export PG_SETEST_CLUSTER=setest_cl
#export PG_SEFOREIGN_CLUSTER=seforeign_cl
#export PG_FILES_CLUSTER=file_cl

export PG_MAIN_PORT=5432
export PG_SETEST_PORT=6000
#export PG_SEFOREIGN_PORT=6001
#export PG_FILES_PORT=6002

export MAIN_DIR=$(cat psb_conf.py | grep 'SCRIPT_DIR =' | awk '{print $3}' | tr -d "'")

# Проверка прав суперпользователя
if ["$UID" -ne "0"]; then
   exit
fi

# Остановка кластеров
pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER stop
# pg_ctlcluster $PG_VERSION $PG_SEFOREIGN_CLUSTER stop
# pg_ctlcluster $PG_VERSION $PG_FILES_CLUSTER stop

# Удаление тестовых пользователей
usermac -d u_0_00 && userdel u_0_00
usermac -d u_1_01 && userdel u_1_01

# Удаление тестового табличного пространства в ФС
rm -rf /var/lib/postgresql/$PG_VERSION/testspace
rm -rf /pg_test_tablespace

# Удаление кластеров
pg_dropcluster $PG_VERSION $PG_SETEST_CLUSTER --stop
# pg_dropcluster $PG_VERSION $PG_SEFOREIGN_CLUSTER --stop
# pg_dropcluster $PG_VERSION $PG_FILES_CLUSTER --stop

rm -rf /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER
# rm -rf /etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER
# rm -rf /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER