#!/usr/bin/env bash
set -vx
export PG_VERSION=9.6
export PG_SETEST_CLUSTER=setest
export PG_SEFOREIGN_CLUSTER=seforeign
export PG_SETEST_PORT=6000
export PG_SEFOREIGN_PORT=6001
export MAIN_DIR=/media/sf_git/skts-test/testlink/postgresql_benchmark

ulimit -c unlimited
# Проверка на наличие прав суперпользователя
if [ "$UID" -ne "0" ]; then
	exit
fi

apt-get install -y postgresql
apt-get install -y postgresql-se-test-${PG_VERSION}

# Подготовка к выполнению тестов
cd /usr/share/postgresql/$PG_VERSION/test/pgacext/

# Создание тестовых пользователей
useradd u_0_00 && usermac -m 0:0 -c 0:0 u_0_00
useradd u_1_01 && usermac -m 1:1 -c 1:1 u_1_01

# Проверка существования и создание кластеров
cluster=`pg_lsclusters | grep "$PG_VERSION $PG_SETEST_CLUSTER"`
foreign=`pg_lsclusters | grep "$PG_VERSION $PG_SEFOREIGN_CLUSTER"`

if [ "x$cluster" == "x" ]
then
	pg_createcluster $PG_VERSION $PG_SETEST_CLUSTER --port $PG_SETEST_PORT
fi

if [ "x$foreign" == "x" ]
then
	pg_createcluster $PG_VERSION $PG_SEFOREIGN_CLUSTER --port $PG_SEFOREIGN_PORT
fi

# Создание тестового табличного пространства в ФС
spc_tst=/var/lib/postgresql/$PG_VERSION/testspace
if [ ! -e $spc_tst ]
then
	mkdir $spc_tst
fi
chown postgres:postgres $spc_tst

# Настройка кластеров
# Настройка конфигурации основного сервера
hba=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
audit=/var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_audit.conf
hba_tst=/usr/share/postgresql/$PG_VERSION/test/pgacext/support/pg_hba.conf.tst
audit_tst=/usr/share/postgresql/$PG_VERSION/test/pgacext/support/pg_audit.conf.tst
setest_old_cfg=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
setest_new_cfg=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf.new

# pg_hint_plan
sed -e "s/#shared_preload_libraries.*/shared_preload_libraries = 'pg_hint_plan'/g" $setest_old_cfg > $setest_new_cfg
mv $setest_new_cfg $setest_old_cfg
cp $hba_tst $hba
cp $audit_tst $audit
chown postgres.postgres /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/*

# Настройка конфигурации внешнего сервера
old_cfg=/etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER/postgresql.conf
new_cfg=/etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER/postgresql.conf.new
sed -e 's/ac_ignore_socket_maclabel.*/ac_ignore_socket_maclabel = false/g' $old_cfg > $new_cfg
mv $new_cfg $old_cfg
foreign_hba=/etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER/pg_hba.conf
foreign_hba_tst=/usr/share/postgresql/$PG_VERSION/test/pgacext/support/pg_hba_foreign.conf.tst
cp $foreign_hba_tst $foreign_hba
chown postgres.postgres /etc/postgresql/$PG_VERSION/$PG_SEFOREIGN_CLUSTER/*

# Перезапуск кластеров
pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER restart
pg_ctlcluster $PG_VERSION $PG_SEFOREIGN_CLUSTER restart

# Настройка необходимых прав пользователю postgres
setfacl -m u:postgres:rx /etc/parsec/macdb
setfacl -m u:postgres:rx /etc/parsec/capdb
setfacl -d -m u:postgres:r /etc/parsec/macdb
setfacl -d -m u:postgres:r /etc/parsec/capdb
setfacl -R -m u:postgres:r /etc/parsec/macdb/*
setfacl -R -m u:postgres:r /etc/parsec/capdb/*

# Создать базу
sql_script=$1
for port in $(pg_lsclusters -h | gawk '{print $3}');
do
  cp $MAIN_DIR/sql/$sql_script /tmp/$sql_script
  cd /tmp
  chmod 644 /tmp/$sql_script
  su -c "psql -p $port -f /tmp/$sql_script" postgres
  rm /tmp/$sql_script
  cd - &> /dev/null
done

