#!/bin/bash
# FAILOVER/FAILBACK скрипт для Pgpool-II (PG11/PG15) без триггерного файла

set -euo pipefail

# Конфигурация
USERNAME="u"
PASSWORD="1"
POSTGRES_PORT=5440
DOMAIN="balance.rbt"
LOGFILE="/var/log/pgpool_failover.log"

# Проверка аргументов
if [ "$#" -ne 8 ]; then
    echo "Usage: $0 <MODE: OVER|BACK> <NEW_MASTER_ID> <NEW_MASTER_HOST> <NEW_MASTER_DATA> <FAILED_NODE_ID> <FAILED_HOST> <OLD_MASTER_ID> <OLD_MASTER_HOST>"
    exit 1
fi

# Параметры
MODE=$1
NEW_MASTER_ID=$2
NEW_MASTER_HOST=$3
NEW_MASTER_DATA=$4
FAILED_NODE_ID=$5
FAILED_HOST=$6
OLD_MASTER_ID=$7
OLD_MASTER_HOST=$8

# SSH-команды с sshpass
SSH_OPTS="-T -o StrictHostKeyChecking=no"
SSHPASS="sshpass -p '$PASSWORD'"
SSH_CMD="$SSHPASS ssh $SSH_OPTS $USERNAME@"

# Логирование
log() {
    echo "[$(date)] $1" >> "$LOGFILE"
}

# Определение версии PostgreSQL
get_pg_version() {
    local host=$1
    $SSH_CMD$host "sudo ls /etc/postgresql" | grep -E '11|15' | head -1 || echo "11"
}

# Продвижение реплики до мастера
promote_replica() {
    local host=$1
    local version=$2
    
    log "Promoting $host (PG$version) to primary"
    
    if [ "$version" == "11" ]; then
        # Для PG11 используем pg_ctl promote
        $SSH_CMD$host "sudo -u postgres /usr/lib/postgresql/11/bin/pg_ctl promote -D $NEW_MASTER_DATA"
    else
        # Для PG15+ используем pg_promote()
        $SSH_CMD$host "sudo -u postgres psql -h localhost -p $POSTGRES_PORT -c 'SELECT pg_promote(true, 60)'"
    fi
    
    # Ждем пока узел станет мастером
    $SSH_CMD$host "until sudo -u postgres psql -h localhost -p $POSTGRES_PORT -c 'SELECT pg_is_in_recovery()' | grep -q -w f; do sleep 1; done"
}

# Обновление реплик
update_replicas() {
    local new_master=$1
    local version=$2
    
    for node in "database1" "database2" "database3"; do
        node_host="$node.$DOMAIN"
        if [ "$node_host" != "$new_master" ]; then
            log "Reconfiguring replica $node_host"
            node_version=$(get_pg_version "$node_host")
            
            # Определяем файл конфигурации в зависимости от версии
            conf_file="$NEW_MASTER_DATA/recovery.conf"
            [ "$node_version" == "15" ] && conf_file="$NEW_MASTER_DATA/postgresql.auto.conf"
            
            $SSH_CMD$node_host "sudo sed -i \"s|host=.* port=.*|host=$new_master port=$POSTGRES_PORT|g\" $conf_file"
            $SSH_CMD$node_host "sudo systemctl restart postgresql@${node_version}-contrprimer.service"
        fi
    done
}

# Основной цикл обработки
case "$MODE" in
  OVER)
    log "Starting FAILOVER: $FAILED_HOST -> $NEW_MASTER_HOST"
    
    # Определяем версию PostgreSQL на новом мастере
    PG_VERSION=$(get_pg_version "$NEW_MASTER_HOST")
    
    # Продвижение нового мастера
    promote_replica "$NEW_MASTER_HOST" "$PG_VERSION"
    
    # Обновление реплик
    update_replicas "$NEW_MASTER_HOST" "$PG_VERSION"
    ;;

  BACK)
    log "Starting FAILBACK: $OLD_MASTER_HOST -> $NEW_MASTER_HOST"
    
    # Определяем версии
    old_version=$(get_pg_version "$OLD_MASTER_HOST")
    new_version=$(get_pg_version "$NEW_MASTER_HOST")
    
    # Остановка и очистка
    $SSH_CMD$OLD_MASTER_HOST "sudo systemctl stop postgresql@${old_version}-contrprimer.service"
    $SSH_CMD$OLD_MASTER_HOST "sudo rm -rf ${NEW_MASTER_DATA}/*"
    
    # Базовый бэкап с автоматической настройкой репликации
    $SSH_CMD$OLD_MASTER_HOST "PGPASSWORD='$PASSWORD' pg_basebackup -h $NEW_MASTER_HOST -p $POSTGRES_PORT -U postgres \
        -D $NEW_MASTER_DATA -Fp -Xs -P -R"
    
    # Запуск службы
    $SSH_CMD$OLD_MASTER_HOST "sudo systemctl start postgresql@${old_version}-contrprimer.service"
    ;;
    
  *)
    log "Invalid mode: $MODE"
    exit 1
    ;;
esac

log "Operation $MODE completed successfully"
exit 0