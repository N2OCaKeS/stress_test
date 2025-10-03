set -vx


PG_VERSION="17"                 
PG_SETEST_CLUSTER="TEST"           
DB_NAME="test"                     
USER="postgres"                    
PORT="6000"                      
DATA_DIR="/var/lib/pgpro/ent-${PG_VERSION}/data" 
CONF_DIR="${DATA_DIR}/postgresql.conf" 
HBA_CONF="${DATA_DIR}/pg_hba.conf"  
BIN_PATH="/opt/pgpro/ent-${PG_VERSION}/bin/" 

# Удаление старых данных и создание каталога
sudo rm -rf $DATA_DIR
sudo mkdir -p "$DATA_DIR"
sudo chown -R postgres:postgres "$DATA_DIR"

# Инициализация кластера
sudo -u postgres ${BIN_PATH}/initdb -D "$DATA_DIR" --auth-local trust --auth-host md5

# Настройка конфигурационного файла
echo "port = 6000" >> "$CONF_DIR"

# Запуск сервера
sudo -u postgres ${BIN_PATH}/pg_ctl -D "$DATA_DIR" -o "-p $PORT" start

# Создание базы данных
sudo -u postgres ${BIN_PATH}/createdb --port=$PORT $DB_NAME

# Перезапуск сервера (необязательно, но рекомендуется после создания)
sudo -u postgres ${BIN_PATH}/pg_ctl -D "$DATA_DIR" -o "-p $PORT" restart

# Создать пользователя и базу данных
sudo -u postgres ${BIN_PATH}/psql "-p $PORT" -c "CREATE USER $USER;"
sudo -u postgres ${BIN_PATH}/psql "-p $PORT" -c "CREATE DATABASE $DB_NAME;"
sudo -u postgres ${BIN_PATH}/psql "-p $PORT" -c "ALTER DATABASE $DB_NAME OWNER TO $USER;"
sudo -u postgres ${BIN_PATH}/psql "-p $PORT" -c "ALTER SCHEMA public OWNER TO $USER;"

# Настройка
sed -i -e 's/md5/trust/g' -e 's/scram-sha-256/trust/g' -e 's/peer/trust/g' "$HBA_CONF"

sed -i 's/#ac_enable_maclabels_on_files.*$/ac_enable_maclabels_on_files = true/' "$CONF_DIR"
sed -i 's/max_connections.*/max_connections = 500/' "$CONF_DIR"
sed -i 's/shared_buffers.*/shared_buffers = 64512MB/' "$CONF_DIR"
sed -i 's/effective_cache_size.*/effective_cache_size = 193536MB/' "$CONF_DIR"
sed -i 's/maintenance_work_mem.*/maintenance_work_mem = 2GB/' "$CONF_DIR"
sed -i 's/checkpoint_completion_target.*/checkpoint_completion_target = 0.9/' "$CONF_DIR"
sed -i 's/wal_buffers.*/wal_buffers = 16MB/' "$CONF_DIR"
sed -i 's/default_statistics_target.*/default_statistics_target = 100/' "$CONF_DIR"
sed -i 's/random_page_cost.*/random_page_cost = 1.1/' "$CONF_DIR"
sed -i 's/effective_io_concurrency.*/effective_io_concurrency = 200/' "$CONF_DIR"
sed -i 's/work_mem.*/work_mem = 82574kB/' "$CONF_DIR"
sed -i 's/min_wal_size.*/min_wal_size = 1GB/' "$CONF_DIR"
sed -i 's/max_wal_size.*/max_wal_size = 4GB/' "$CONF_DIR"
sed -i 's/max_worker_processes.*/max_worker_processes = 32/' "$CONF_DIR"
sed -i 's/max_parallel_workers_per_gather.*/max_parallel_workers_per_gather = 4/' "$CONF_DIR"
sed -i 's/max_parallel_workers.*/max_parallel_workers = 32/' "$CONF_DIR"
sed -i 's/max_parallel_maintenance_workers.*/max_parallel_maintenance_workers = 4/' "$CONF_DIR"

sudo -u postgres ${BIN_PATH}/pg_ctl -D "$DATA_DIR" -m fast restart


sudo -u postgres ${BIN_PATH}/pgbench -i -h localhost -p 6000 -U $USER -s 100 $DB_NAME

#sudo perf record -g -a /opt/pgpro/ent-17/bin/pgbench -h localhost -p 6000 -U postgres -t 1000 -j 200 -c 200 test