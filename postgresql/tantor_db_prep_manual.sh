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

sed -i "s/^#\?\s*max_connections\s*=.*/max_connections = 500/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*shared_buffers\s*=.*/shared_buffers = 32256MB/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*effective_cache_size\s*=.*/effective_cache_size = 96768MB/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*maintenance_work_mem\s*=.*/maintenance_work_mem = 2GB/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*checkpoint_completion_target\s*=.*/checkpoint_completion_target = 0.9/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*wal_buffers\s*=.*/wal_buffers = 16MB/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*default_statistics_target\s*=.*/default_statistics_target = 100/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*random_page_cost\s*=.*/random_page_cost = 1.1/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*effective_io_concurrency\s*=.*/effective_io_concurrency = 200/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*min_wal_size\s*=.*/min_wal_size = 1GB/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_wal_size\s*=.*/max_wal_size = 4GB/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_worker_processes\s*=.*/max_worker_processes = 32/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_parallel_workers_per_gather\s*=.*/max_parallel_workers_per_gather = 4/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_parallel_workers\s*=.*/max_parallel_workers = 32/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i "s/^#\?\s*max_parallel_maintenance_workers\s*=.*/max_parallel_maintenance_workers = 4/" /var/lib/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
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

