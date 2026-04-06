#!/bin/bash
# failover.sh — выполняет failover в кластере PostgreSQL и обновляет конфиг на всех нодах.
set -o xtrace
set -e

FAILED_NODE_ID="$1"
FAILED_NODE_HOST="$2"
FAILED_NODE_PORT="$3"
FAILED_NODE_PGDATA="$4"
NEW_MAIN_NODE_ID="$5"
NEW_MAIN_NODE_HOST="$6"
OLD_MAIN_NODE_ID="$7"
OLD_PRIMARY_NODE_ID="$8"
NEW_MAIN_NODE_PORT="$9"
NEW_MAIN_NODE_PGDATA="${10}"
OLD_PRIMARY_NODE_HOST="${11}"
OLD_PRIMARY_NODE_PORT="${12}"

# Настройки
DATABASE_NODES=("${13}" "${14}" "${15}")
SSH_USER="u"
PASSWORD="1"
PG_PORT=5440

# Определяем версию PostgreSQL по Astra
OS_VERSION=$(cut -d. -f1,2 /etc/astra_version)
if [ "$OS_VERSION" == "1.7" ]; then
    VERSION_PG=11
elif [ "$OS_VERSION" == "1.8" ]; then
    VERSION_PG=15
else
    echo "Unsupported Astra Linux version: $OS_VERSION"
    exit 1
fi

PG_PATH="/usr/lib/postgresql/${VERSION_PG}/bin"
SSHPASS="sshpass -p $PASSWORD"
REPLUSER="postgres" # Имя пользователя репликации
SSH_OPTS="-T -o ControlMaster=auto -o ControlPersist=2m -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"

echo "failover.sh: start: failed_node_id=$FAILED_NODE_ID failed_host=$FAILED_NODE_HOST old_primary_node_id=$OLD_PRIMARY_NODE_ID new_main_node_id=$NEW_MAIN_NODE_ID new_main_host=$NEW_MAIN_NODE_HOST"

# Если упала реплика (не мастер) - ничего не делаем
if [ "$FAILED_NODE_ID" != "$OLD_PRIMARY_NODE_ID" ]; then
    echo "failover.sh: Failed node is not primary (it's a replica). No action needed."
    exit 0
fi

if [ "$NEW_MAIN_NODE_ID" -lt 0 ]; then
    echo "failover.sh: All nodes are down. Skipping failover."
    exit 0
fi

# Проверка SSH к новому мастеру
$SSHPASS ssh $SSH_OPTS $SSH_USER@$NEW_MAIN_NODE_HOST ls /tmp > /dev/null
if [ $? -ne 0 ]; then
    echo "failover.sh: SSH to $SSH_USER@$NEW_MAIN_NODE_HOST failed. Please check password or access."
    exit 1
fi

# Отключение постгрес у старого мастера
$SSHPASS ssh $SSH_OPTS $SSH_USER@$FAILED_NODE_HOST bash <<EOF
set -e
sudo systemctl stop postgresql@${VERSION_PG}-contrprimer.service
EOF


# Промоут нового мастера
echo "failover.sh: primary node is down, promote new_main_node_id=$NEW_MAIN_NODE_ID on $NEW_MAIN_NODE_HOST."
$SSHPASS ssh $SSH_OPTS $SSH_USER@$NEW_MAIN_NODE_HOST bash <<EOF
set -e
sudo su - postgres -c "$PG_PATH/pg_ctl -D $NEW_MAIN_NODE_PGDATA -w promote"
EOF

if [ $? -ne 0 ]; then
    echo "ERROR: failover.sh: end: failover failed"
    exit 1
fi

echo "failover.sh: end: new_main_node_id=$NEW_MAIN_NODE_ID on $NEW_MAIN_NODE_HOST is promoted to a primary"

update_nodes_after_failover() {
    echo "=== Обновляем реплики после failover ==="

    for node in "${DATABASE_NODES[@]}"; do
        echo "Настраиваем ноду $node"
        
        # Пропускаем новый мастер
        if [[ "$node" == "$NEW_MAIN_NODE_HOST" ]]; then
            echo "Пропускаем $node (это текущий мастер)"
            continue
        fi

        NODE_PGDATA="/var/lib/postgresql/${VERSION_PG}/contrprimer"
        NODE_PG_CONFIG="/etc/postgresql/${VERSION_PG}/contrprimer"

        if [[ "${VERSION_PG}" -ge 12 ]]; then
            echo "PG${VERSION_PG}: standby.signal + primary_conninfo для $node"

            # Останавливаем PostgreSQL
            # $SSHPASS ssh $SSH_OPTS $SSH_USER@$node sudo systemctl stop postgresql@${VERSION_PG}-contrprimer.service

            # Удаляем старый primary_conninfo из postgresql.auto.conf (если есть)
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node "sudo -u postgres rm -f ${NODE_PGDATA}/postgresql.auto.conf"

            # Создаём новый postgresql.auto.conf с primary_conninfo через tee
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node "sudo -u postgres tee ${NODE_PGDATA}/postgresql.auto.conf >/dev/null" <<EOF
primary_conninfo = 'user=postgres passfile=''/var/lib/postgresql/.pgpass'' host=${NEW_MAIN_NODE_HOST} port=5440 sslmode=prefer sslcompression=0 krbsrvname=postgres target_session_attrs=any'
recovery_target_timeline = 'latest'
EOF

            # Убеждаемся, что standby.signal существует (для режима реплики)
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node "sudo -u postgres touch ${NODE_PGDATA}/standby.signal"

            # Запускаем PostgreSQL
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node sudo systemctl start postgresql@${VERSION_PG}-contrprimer.service
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node "cd /tmp && sudo -u postgres psql -p $PG_PORT -c 'SELECT pg_reload_conf();'"
            echo "Нода $node (PG${VERSION_PG}) настроена и перезапущена как реплика -> новый мастер: ${NEW_MAIN_NODE_HOST}"

            # Перезагружаем конфигурацию (на всякий случай)

        else
            # Код для PostgreSQL 11 (оставлен без изменений)
            # $SSHPASS ssh $SSH_OPTS $SSH_USER@$node sudo systemctl stop postgresql@11-contrprimer.service
            echo "PG${VERSION_PG}: recovery.conf для $node"
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node "sudo rm -f ${NODE_PGDATA}/recovery.conf"
            # $SSHPASS ssh $SSH_OPTS $SSH_USER@$node "sudo rm -f /var/lib/postgresql/11/contrprimer/postmaster.pid"
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node "sudo tee ${NODE_PGDATA}/recovery.conf >/dev/null" <<EOF
standby_mode = 'on'
primary_conninfo = 'user=${REPLUSER} passfile=''/var/lib/postgresql/.pgpass'' host=${NEW_MAIN_NODE_HOST} port=${PG_PORT} sslmode=prefer sslcompression=0 krbsrvname=postgres target_session_attrs=any'
recovery_target_timeline = 'latest'
EOF
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node "cd /tmp && sudo -u postgres psql -p $PG_PORT -c 'SELECT pg_reload_conf();'"
            $SSHPASS ssh $SSH_OPTS $SSH_USER@$node sudo systemctl start postgresql@11-contrprimer.service
            echo "Нода $node (PG${VERSION_PG}) настроена и перезапущена как реплика -> новый мастер: ${NEW_MAIN_NODE_HOST}"
        fi
    done
    echo "=== Все реплики настроены ==="
}

update_nodes_after_failover

exit 0