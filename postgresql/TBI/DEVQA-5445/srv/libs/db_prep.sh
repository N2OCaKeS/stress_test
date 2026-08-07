#!/bin/bash

set -vx

PG_MAIN_CLUSTER=main
PG_MAIN_PORT=5432
PG_VERSION=$1
SCRIPT_DIR=$2
PG_SETEST_CLUSTER='setest_cl'
PG_SETEST_PORT=$3


{
    apt update &&
    apt-get install -y postgresql-${PG_VERSION}
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi

#Создаем пользователя
{   
    useradd u_1 &&
    pdpl-user -i 63 u_1 &&
    pdpl-user -i 63 postgres &&
    pdpl-user -l 0:255 -c 0:0xFFFFFFFFFFFFFFFF u_1 &&
    usercaps -m PARSEC_CAP_CHMAC:PARSEC_CAP_SETMAC u_1
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-# Создание кластеров #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Проверка существования и создание кластеров
{
    test_cluster=`pg_lsclusters | grep "$PG_VERSION $PG_SETEST_CLUSTER"`
    if [ "x$test_cluster" == "x" ]; then
        pg_createcluster $PG_VERSION $PG_SETEST_CLUSTER --port $PG_SETEST_PORT
    fi
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-# Настройка кластеров #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
### Настройка конфигурации основного сервера ###
{
    hba=/etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf &&
    audit=/var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_audit.conf &&
    chown -R postgres:postgres /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/*
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi

{
    #sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 5000/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*shared_buffers\s*=.*/shared_buffers = $4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*effective_cache_size\s*=.*/effective_cache_size = $5/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*maintenance_work_mem\s*=.*/maintenance_work_mem = 2GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*checkpoint_completion_target\s*=.*/checkpoint_completion_target = 0.9/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*wal_buffers\s*=.*/wal_buffers = 16MB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*default_statistics_target\s*=.*/default_statistics_target = 100/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*random_page_cost\s*=.*/random_page_cost = 1.1/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*effective_io_concurrency\s*=.*/effective_io_concurrency = 200/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*work_mem\s*=.*/work_mem = $6/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*min_wal_size\s*=.*/min_wal_size = 1GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*max_wal_size\s*=.*/max_wal_size = 4GB/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*max_worker_processes\s*=.*/max_worker_processes = $7/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*max_parallel_workers_per_gather\s*=.*/max_parallel_workers_per_gather = 4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*max_parallel_workers\s*=.*/max_parallel_workers = 32/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i "s/^#\?\s*max_parallel_maintenance_workers\s*=.*/max_parallel_maintenance_workers = 4/" /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf &&
    sed -i -e 's/md5/trust/g' -e 's/scram-sha-256/trust/g' -e 's/peer/trust/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-



#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Перезапуск кластеров
{
    pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER restart &&
    pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER restart
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi

# Настройка необходимых прав пользователю postgres
{
    usermod -a -G shadow postgres &&
    setfacl -d -m u:postgres:r /etc/parsec/macdb &&
    setfacl -R -m u:postgres:r /etc/parsec/macdb &&
    setfacl -m u:postgres:rx /etc/parsec/macdb &&
    setfacl -d -m u:postgres:r /etc/parsec/capdb &&
    setfacl -R -m u:postgres:r /etc/parsec/capdb &&
    setfacl -m u:postgres:rx /etc/parsec/capdb
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi


#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#- Создать БД #-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-
#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-

# Создать базу
{
    sql_script_add_user=psb_add_user.sql &&
    sql_script_set_mac=psb_parsec.sql
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi

# Удалить main
{
    pg_ctlcluster $PG_VERSION $PG_MAIN_CLUSTER stop &&
    pg_dropcluster $PG_VERSION $PG_MAIN_CLUSTER --stop &&
    rm -rf /etc/postgresql/$PG_VERSION/$PG_MAIN_CLUSTER
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi

{
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
}
if [ "$?" != "0" ]; then
    echo "Ошибка при выполнении блока команд: $(($?))"
    exit 1
fi

