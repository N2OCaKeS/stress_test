#!/bin/bash
# Скрипт для выполнения процедуры failover с использованием ALTER SYSTEM для обновления primary_conninfo

# Параметры, передаваемые в скрипт:
# $1   FAILED_NODE_ID         - ID узла, потерявшего связь
# $2   FAILED_NODE_HOST       - Hostname узла, потерявшего связь
# $3   FAILED_NODE_PORT       - Порт узла, потерявшего связь
# $4   FAILED_NODE_DIR        - Каталог данных узла, потерявшего связь
# $5   NEW_MASTER_NODE_ID     - ID нового мастера (ожидаемая нода)
# $6   NEW_MASTER_NODE_HOST   - Hostname нового мастера
# $7   NEW_MASTER_NODE_PORT   - Порт нового мастера
# $8   NEW_MASTER_NODE_DIR    - Каталог данных нового мастера
# $9   NEW_MASTER_NODE_FLAG   - Флаг нового мастера (не используется)
# ${10} OLD_MASTER_NODE_ID     - ID старого мастера
# ${11} OLD_MASTER_NODE_HOST   - Hostname старого мастера

FAILED_NODE_ID=$1
FAILED_NODE_HOST=$2
FAILED_NODE_PORT=$3
FAILED_NODE_DIR=$4
NEW_MASTER_NODE_ID=$5
NEW_MASTER_NODE_HOST=$6
NEW_MASTER_NODE_PORT=$7
NEW_MASTER_NODE_DIR=$8
NEW_MASTER_NODE_FLAG=$9
OLD_MASTER_NODE_ID=${10}
OLD_MASTER_NODE_HOST=${11}

# Локальные переменные – заполните их под своё окружение
SSH_USER="u"
SSH_PASS="1"
# Массив standby-узлов (укажите хостнеймы или IP-адреса)
DATABASES=("10.177.103.112" "10.177.103.113")
# Порт PostgreSQL для подключения
PG_PORT=5440
# Пользователь для подключения и выполнения psql
REPL_USER="postgres"

LOG_FILE="/var/log/pgpool/cluster_failover.log"
DATE=$(date "+%F %T")

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "$DATE $1" >> "$LOG_FILE"
}

echo "!!! FAILOVER START !!!"
echo "WARNING: Lost connection with $FAILED_NODE_HOST [id:$FAILED_NODE_ID]"
echo "Old master: $OLD_MASTER_NODE_HOST [id:$OLD_MASTER_NODE_ID]"
echo "New master: $NEW_MASTER_NODE_HOST [id:$NEW_MASTER_NODE_ID]"

log "Starting failover procedure. Lost connection with $FAILED_NODE_HOST."

# Если ID старого мастера совпадает с ID нового, значит отказ произошёл не у мастера (отказ standby‑ноды) – промоция не требуется.
if [ "$OLD_MASTER_NODE_ID" -eq "$NEW_MASTER_NODE_ID" ]; then
    log "No failover required – old and new master are identical."
    echo "Old and new master are identical, no promotion needed."
    exit 0
fi

log "FAILOVER: Promoting $NEW_MASTER_NODE_HOST as master because $FAILED_NODE_HOST is unavailable."

# Подготовка SSH-команды (sshpass используется для автоматического ввода пароля)
SSHPASS="sshpass -p '$SSH_PASS'"
SSH="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
   -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
   -o StrictHostKeyChecking=no $SSH_USER@$NEW_MASTER_NODE_HOST"

echo "Promoting standby node to master..."
$SSH sudo touch "$NEW_MASTER_NODE_DIR/failover"

# Обновление настроек подключения (primary_conninfo) на всех standby‑узлах через ALTER SYSTEM
for node in "${DATABASES[@]}"; do
    echo "Configuring node $node"
    SSH_NODE="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
       -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
       -o StrictHostKeyChecking=no $SSH_USER@$node"
    # Задаём параметр primary_conninfo с новым мастером
    $SSH_NODE sudo -u postgres psql -p $PG_PORT -c "ALTER SYSTEM SET primary_conninfo = 'host=$NEW_MASTER_NODE_HOST port=$NEW_MASTER_NODE_PORT user=$REPL_USER';"
    # Применяем изменения – перезагружаем конфигурацию
    $SSH_NODE sudo -u postgres psql -p $PG_PORT -c 'SELECT pg_reload_conf();'
done

echo "!!! FAILOVER FINISH !!!"
log "Failover procedure completed. New master: $NEW_MASTER_NODE_HOST."
exit 0
