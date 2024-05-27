#!/bin/bash
set -vx

PG_VERSION=$1
PG_SETEST_CLUSTER=TEST
DB_NAME=test
USER=postgres

pg_createcluster $PG_VERSION $PG_SETEST_CLUSTER --port 6000                             
rm -r /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/*
chown -R postgres:postgres /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER

sudo -u postgres -i << EOF
/usr/lib/postgresql/$PG_VERSION/bin/initdb -D /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER --auth-local trust --auth-host md5
EOF

pg_ctlcluster $PG_VERSION $PG_SETEST_CLUSTER restart
pg_dropcluster $PG_VERSION main --stop
rm -rf /etc/postgresql/$PG_VERSION/main
pg_lsclusters

sudo -u postgres -i << EOF
psql -c "CREATE USER $USER;"
psql -c "CREATE DATABASE $DB_NAME;"
psql -c "ALTER DATABASE $DB_NAME OWNER TO $USER;"
psql -c "ALTER SCHEMA public OWNER TO $USER;"
EOF

sed -i -e 's/md5/trust/g' -e 's/scram-sha-256/trust/g' -e 's/peer/trust/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf
sed -i 's/ac_enable_maclabels_on_files.*/ac_enable_maclabels_on_files = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_connections.*/max_connections = 500/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*shared_buffers.*/shared_buffers = 64512MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_cache_size.*/effective_cache_size = 193536MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*maintenance_work_mem.*/maintenance_work_mem = 2GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*wal_buffers.*/wal_buffers = 16MB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*default_statistics_target.*/default_statistics_target = 100/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*random_page_cost.*/random_page_cost = 1.1/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_io_concurrency.*/effective_io_concurrency = 200/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*work_mem.*/work_mem = 82574kB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_worker_processes.*/max_worker_processes = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 32/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
systemctl restart postgresql.service


pgbench -i -h localhost -p 6000 -U $USER -s 100 $DB_NAME
#pgbench -h localhost -p 6000 -U $USER -t 1000 -j 200 -c 200 $DB_NAME

cat << EOF > start_test.sh
#!/bin/bash
clients="200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200"
t=30
dir=test
mkdir \$dir
for c in \$clients; do
    echo "pgbench_\${c}_\${t}.txt"
    echo "start test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "\${dir}/pgbench_\${c}.txt"
    pgbench -h localhost -p 6000 -U $USER --random-seed=13 -T \$t -j \$c -c \$c $DB_NAME >> "\${dir}/pgbench_result.txt"
    echo "stop test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "\${dir}/pgbench_\${c}.txt"
done
EOF


# TODO: Возможно стоит дополнить тесты проверкой 
# Способ проверки записей в auditd относительно количества транзакций
# date && pgbench -h localhost -p 6000 -U postgres -t 10 -j 200 -c 200 test && date
# Чт 21 мар 2024 20:11:08 MSK
# pgbench (15.4 (Debian 15.4-astra.se8))
# starting vacuum...end.
# transaction type: <builtin: TPC-B (sort of)>
# scaling factor: 100
# query mode: simple
# number of clients: 200
# number of threads: 200
# maximum number of tries: 1
# number of transactions per client: 10
# number of transactions actually processed: 2000/2000
# number of failed transactions: 0 (0.000%)
# latency average = 47.683 ms
# initial connection time = 156.416 ms
# tps = 4194.358169 (without initial connection time)
# Чт 21 мар 2024 20:11:09 MSK
# ausearch -i -ts "20:11:08" -te "20:11:09" -m user_avc | less | wc -l
# 10042
