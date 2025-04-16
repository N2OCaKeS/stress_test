#!/bin/bash
# FAILOVER скрипт для PGpool
#
# Использование:
#   ./failover.sh <NEW_MASTER_ID> <NEW_MASTER_HOST> <NEW_MASTER_DATA> <FAILED_NODE_ID> <FAILED_HOST> <OLD_MASTER_ID> <OLD_MASTER_HOST>
#
# Эти параметры передаются PGpool:
#   NEW_MASTER_ID    - ID новой ноды, которая должна стать мастером
#   NEW_MASTER_HOST  - FQDN новой ноды (например, database1.balance.rbt)
#   NEW_MASTER_DATA  - Путь к каталогу данных PostgreSQL на новой ноде
#   FAILED_NODE_ID   - ID ноды, потерявшей связь
#   FAILED_HOST      - FQDN ноды, потерявшей связь
#   OLD_MASTER_ID    - ID старого мастера
#   OLD_MASTER_HOST  - FQDN старого мастера

# Проверка количества аргументов
if [ "$#" -ne 7 ]; then
    echo "Usage: $0 <NEW_MASTER_ID> <NEW_MASTER_HOST> <NEW_MASTER_DATA> <FAILED_NODE_ID> <FAILED_HOST> <OLD_MASTER_ID> <OLD_MASTER_HOST>"
    exit 1
fi

# Параметры, полученные от PGpool
NEW_MASTER_ID=$1
NEW_MASTER_HOST=$2
NEW_MASTER_DATA=$3
FAILED_NODE_ID=$4
FAILED_HOST=$5
OLD_MASTER_ID=$6
OLD_MASTER_HOST=$7

# Жёстко заданные настройки
USERNAME="u"
PASSWORD="1"
POSTGRES_PORT=5440
DOMAIN="balance.rbt"

# Список нод базы данных (из группы "database")
DATABASE_NODES=("database1" "database2" "database3")

# Подготовка SSH-команды с использованием sshpass
SSHPASS="sshpass -p $PASSWORD"
SSH="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
     -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
     -o StrictHostKeyChecking=no $USERNAME@$NEW_MASTER_HOST"

echo "!!! FAILOVER START !!!"
echo "Параметры, полученные от PGpool:"
echo "  Новый мастер: $NEW_MASTER_HOST (ID: $NEW_MASTER_ID, Data: $NEW_MASTER_DATA)"
echo "  Провалившаяся нода: $FAILED_HOST (ID: $FAILED_NODE_ID)"
echo "  Старый мастер: $OLD_MASTER_HOST (ID: $OLD_MASTER_ID)"

# Если новый мастер отличается от старого и его ID больше или равен 0, проводим failover
if [ "$OLD_MASTER_ID" -ne "$NEW_MASTER_ID" ] && [ "$NEW_MASTER_ID" -ge 0 ]; then
    echo "Начинается продвижение новой ноды в мастер..."
    $SSH sudo touch "$NEW_MASTER_DATA/failover"

    # Извлекаем суффикс домена (для DOMAIN="balance.rbt" получается "rbt")
    domain_suffix=$(echo "$DOMAIN" | cut -d'.' -f2)

    # Перебор нод для обновления конфигурации
    for node in "${DATABASE_NODES[@]}"; do
        node_fqdn="${node}.${DOMAIN}"
        echo "Настройка ноды: $node_fqdn"
        SSH_NODE="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
-o StrictHostKeyChecking=no $USERNAME@$node_fqdn"
        # Обновляем параметр host в конфигурационном файле, заменяя его на новый мастер
        $SSH_NODE sudo sed -i "s/host=.*${domain_suffix}/host=$NEW_MASTER_HOST/" "$NEW_MASTER_DATA/postgresql.auto.conf"
        # Если нода не является ни старым, ни новым мастером, перезагружаем конфигурацию PostgreSQL
        if [ "$node_fqdn" != "$OLD_MASTER_HOST" ] && [ "$node_fqdn" != "$NEW_MASTER_HOST" ]; then
            $SSH_NODE "sudo -u postgres psql -p $POSTGRES_PORT -c 'SELECT pg_reload_conf();'"
        fi
    done
fi

echo "!!! FAILOVER FINISH !!!"
exit 0
