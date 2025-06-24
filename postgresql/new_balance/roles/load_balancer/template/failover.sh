#!/bin/bash
# This script is run by failover_command.

set -o xtrace

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

SSH_USERNAME="u"
USERNAME="postgres"
PASSWORD="1"
POSTGRES_PORT=5440
DOMAIN="balance.rbt"
REPL_SLOT_NAME="${FAILED_NODE_HOST//[-.]/_}"

# Определение версии PostgreSQL из версии Astra Linux
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

echo failover.sh: start: failed_node_id=$FAILED_NODE_ID failed_host=$FAILED_NODE_HOST \
    old_primary_node_id=$OLD_PRIMARY_NODE_ID new_main_node_id=$NEW_MAIN_NODE_ID new_main_host=$NEW_MAIN_NODE_HOST

# Если нет нового мастера — выходим
if [ "$NEW_MAIN_NODE_ID" -lt 0 ]; then
    echo failover.sh: All nodes are down. Skipping failover.
    exit 0
fi

# Проверка SSH-доступа
sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ${SSH_USERNAME}@${NEW_MAIN_NODE_HOST} ls /tmp > /dev/null
if [ $? -ne 0 ]; then
    echo failover.sh: SSH to ${SSH_USERNAME}@${NEW_MAIN_NODE_HOST} failed. Please check password or access.
    exit 1
fi

# Промоут нового мастера
echo failover.sh: primary node is down, promote new_main_node_id=$NEW_MAIN_NODE_ID on ${NEW_MAIN_NODE_HOST}.

sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ${SSH_USERNAME}@${NEW_MAIN_NODE_HOST} bash <<EOF
set -e

# Промоут через pg_ctl
sudo su - postgres -c "${PG_PATH}/pg_ctl -D ${NEW_MAIN_NODE_PGDATA} -w promote"
EOF

if [ $? -ne 0 ]; then
    echo "ERROR: failover.sh: end: failover failed"
    exit 1
fi

echo failover.sh: end: new_main_node_id=$NEW_MAIN_NODE_ID on ${NEW_MAIN_NODE_HOST} is promoted to a primary
exit 0
