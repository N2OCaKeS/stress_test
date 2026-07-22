#!/bin/bash
# Usage: ./start_test.sh "1 2 4 8 16"
# Argument: space-separated list of client counts to test sequentially

ulimit -n 65536

# clients="$1"
clients="800 800 800 800 800 800 800 800 800 800 800 800 800 800 800 800 800 800 800 800"
# clients="200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200"
t=30
dir=test
threads=$(nproc)

# TODO !!!!!!!
psql_version=15
#--------------**********************----------

mkdir -p $dir

for c in $clients; do
    echo "pgbench_${c}_${t}.txt"
    echo "start test: "$(date +"%Y.%m.%d_%H:%M:%S") >> "${dir}/pgbench_${c}.txt"
    # ДЛЯ ASTRA PARSEC
    # pgbench -h localhost --macs=fixed -p 6000 -U u_1 --random-seed=13 -T $t -j $threads -c $c test_parsec >> "${dir}/pgbench_result.txt"

    # ДЛЯ RHEL или REDOS
    # /usr/pgsql-${psql_version}/bin/pgbench -h localhost -p 5433 -U postgres --random-seed=13 -T $t -j $threads -c $c test_parsec >> "${dir}/pgbench_result.txt"

    # ДЛЯ ASTRA или DEBIAN или ALT
    #pgbench -h localhost -p 5433 -U postgres --random-seed=13 -T $t -j $threads -c $c test_parsec >> "${dir}/pgbench_result.txt"
    echo "stop test: "$(date +"%Y.%m.%d_%H:%M:%S") >> "${dir}/pgbench_${c}.txt"
done

