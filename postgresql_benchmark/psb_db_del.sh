#!/usr/bin/env bash

export PG_VERSION=$1
export PG_SETEST_CLUSTER=setest
export PG_SEFOREIGN_CLUSTER=seforeign
export PG_FILES_CLUSTER=pg_test
export MAIN_DIR=/media/sf_git/skts-test/testlink/postgresql_benchmark

# Проверка прав суперпользователя
if ["$UID" -ne "0"]; then
   exit
fi

# Остановка кластеров
pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER stop
pg_ctlcluster $PG_VERSION $PG_SEFOREIGN_CLUSTER stop
pg_ctlcluster $PG_VERSION $PG_FILES_CLUSTER stop

# Удаление тестовых пользователей
usermac -d u_0_00 && userdel u_0_00
usermac -d u_1_01 && userdel u_1_01

# Удаление тестового табличного пространства в ФС
rm -rf /var/lib/postgresql/$PG_VERSION/testspace
rm -rf /pg_test_tablespace

# Удаление кластеров
pg_dropcluster $PG_VERSION $PG_SETEST_CLUSTER --stop
pg_dropcluster $PG_VERSION $PG_SEFOREIGN_CLUSTER --stop
pg_dropcluster $PG_VERSION $PG_FILES_CLUSTER --stop

rm -rf /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER
rm -rf /etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER
rm -rf /etc/postgresql/$PG_VERSION/$PG_FILES_CLUSTER