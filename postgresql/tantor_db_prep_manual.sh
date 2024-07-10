#!/bin/bash
set -vx

PG_VERSION=tantor-se-15
PG_SETEST_CLUSTER=data
DB_NAME=test
USER=postgres


chown -R postgres:postgres /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER
sudo -u postgres -i << EOF
/opt/tantor/db/15/bin/initdb -D /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER --auth-local trust --auth-host md5
EOF

systemctl start tantor-se-server-15

sudo -u postgres -i << EOF
psql -c "CREATE USER $USER;"
psql -c "CREATE DATABASE $DB_NAME;"
psql -c "ALTER DATABASE $DB_NAME OWNER TO $USER;"
psql -c "ALTER SCHEMA public OWNER TO $USER;"
EOF

sed -i 's/.*max_connections.*/max_connections = 500/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*shared_buffers.*/shared_buffers = 32256MB/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_cache_size.*/effective_cache_size = 96768MB/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*maintenance_work_mem.*/maintenance_work_mem = 2GB/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*checkpoint_completion_target.*/checkpoint_completion_target = 0.9/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*wal_buffers.*/wal_buffers = 16MB/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*default_statistics_target.*/default_statistics_target = 100/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*random_page_cost.*/random_page_cost = 1.1/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*effective_io_concurrency.*/effective_io_concurrency = 200/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*min_wal_size.*/min_wal_size = 1GB/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_wal_size.*/max_wal_size = 4GB/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_worker_processes.*/max_worker_processes = 32/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_workers.*/max_parallel_workers = 32/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i -e 's/md5/trust/g' -e 's/scram-sha-256/trust/g' -e 's/peer/trust/g' /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/pg_hba.conf


systemctl restart tantor-se-server-15

/opt/tantor/db/15/bin/pgbench -i -h localhost -s 500 -p 5432 -F 100 -U postgres test

cat << EOF > start_test.sh
#!/bin/bash
clients="200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200"
t=30
dir=test
mkdir \$dir
for c in \$clients; do
    echo "pgbench_\${c}_\${t}.txt"
    echo "start test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "\${dir}/pgbench_\${c}.txt"
    /opt/tantor/db/15/bin/pgbench -h localhost -p 5432 -U $USER --random-seed=13 -T \$t -j \$c -c \$c $DB_NAME >> "\${dir}/pgbench_result.txt"
    echo "stop test: "`date +"%Y.%m.%d_%H:%M:%S"` >> "\${dir}/pgbench_\${c}.txt"
done
EOF

