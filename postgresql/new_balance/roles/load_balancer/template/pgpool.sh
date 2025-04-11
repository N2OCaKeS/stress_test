#!/bin/bash

FAILED_NODE_ID=$1
FAILED_NODE_HOST=$2
FAILED_NODE_PORT=$3
FAILED_NODE_DIR=$4
NEW_MASTER_NODE_ID=$5
NEW_MASTER_NODE_HOST=$6
NEW_MASTER_NODE_PORT=$7
NEW_MASTER_NODE_DIR=$8
NEW_MASTER_NODE_FLAG=$9

DATA_DIR="/var/lib/postgresql/11/contrprimer"
REPL_USER="postgres"
PG_SERVICE="postgresql@11-contrprimer"
LOG_FILE="/var/log/pgpool/cluster_failover.log"
DATE=$(date "+%F %T")

mkdir -p "$(dirname $LOG_FILE)"

log() {
    echo "$DATE $1" >> "$LOG_FILE"
}

# Проверяем, мастер ли сломался (failover) или это возврат старого мастера (failback)
if [ "$FAILED_NODE_ID" = "$NEW_MASTER_NODE_ID" ]; then
    log "FAILBACK: Rejoining node $FAILED_NODE_HOST as replica from $NEW_MASTER_NODE_HOST"

    ssh u@"$FAILED_NODE_HOST" "sudo systemctl stop $PG_SERVICE"

    # Резервная копия текущих данных (если остались)
    ssh u@"$FAILED_NODE_HOST" "sudo mv $DATA_DIR ${DATA_DIR}_backup_$(date +%F_%T)"

    # Копируем с текущего мастера
    ssh u@"$FAILED_NODE_HOST" "sudo -u postgres pg_basebackup -h $NEW_MASTER_NODE_HOST -p $NEW_MASTER_NODE_PORT -D $DATA_DIR -U $REPL_USER -P --wal-method=stream" >> $LOG_FILE 2>&1

    # Создаём standby.signal (PG >= 12)
    ssh u@"$FAILED_NODE_HOST" "sudo touch $DATA_DIR/standby.signal"

    # Настраиваем primary_conninfo
    ssh u@"$FAILED_NODE_HOST" "echo \"primary_conninfo = 'host=$NEW_MASTER_NODE_HOST port=$NEW_MASTER_NODE_PORT user=$REPL_USER'\" | sudo tee -a $DATA_DIR/postgresql.auto.conf"

    ssh u@"$FAILED_NODE_HOST" "sudo systemctl start $PG_SERVICE"

    if [ $? -eq 0 ]; then
        log "FAILBACK SUCCESS: $FAILED_NODE_HOST успешно восстановлен как реплика."
        exit 0
    else
        log "FAILBACK ERROR: восстановление не удалось на $FAILED_NODE_HOST"
        exit 1
    fi

else
    log "FAILOVER: Промоция $NEW_MASTER_NODE_HOST в мастер, т.к. $FAILED_NODE_HOST недоступен."

    ssh u@"$NEW_MASTER_NODE_HOST" "sudo su postgres -c 'pg_ctl promote -D $DATA_DIR'" >> $LOG_FILE 2>&1

    if [ $? -eq 0 ]; then
        log "FAILOVER SUCCESS: $NEW_MASTER_NODE_HOST успешно промотирован."
        exit 0
    else
        log "FAILOVER ERROR: ошибка при промоции $NEW_MASTER_NODE_HOST"
        exit 1
    fi
fi
