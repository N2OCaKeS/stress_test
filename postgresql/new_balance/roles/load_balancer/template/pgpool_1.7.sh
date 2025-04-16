#!/bin/bash
# FAILOVER / FAILBACK скрипт для Pgpool-II

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

# Конфигурация
USERNAME="u"
PASSWORD="1"
POSTGRES_PORT=5440
DOMAIN="balance.rbt"
DATABASE_NODES=("database1" "database2" "database3")
LOGFILE="/var/log/pgpool_failover.log"

# SSH команды
SSHPASS="sshpass -p $PASSWORD"

SSH_NEW="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
-o StrictHostKeyChecking=no $USERNAME@$NEW_MASTER_HOST"

SSH_OLD="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
-o StrictHostKeyChecking=no $USERNAME@$OLD_MASTER_HOST"

# Логирование
echo "[$(date)] MODE: $MODE | NEW_MASTER: $NEW_MASTER_HOST | FAILED: $FAILED_HOST | OLD_MASTER: $OLD_MASTER_HOST" >> "$LOGFILE"

echo ">>> [$MODE] START"

case "$MODE" in
    OVER)
        if [ "$OLD_MASTER_ID" -ne "$NEW_MASTER_ID" ] && [ "$NEW_MASTER_ID" -ge 0 ]; then
            echo "[$(date)] Promoting $NEW_MASTER_HOST to primary..." >> "$LOGFILE"
            $SSH_NEW /usr/lib/postgresql/11/bin/pg_ctl promote -D /var/lib/postgresql/11/contrprimer/

            domain_suffix=$(echo "$DOMAIN" | cut -d'.' -f2)

            for node in "${DATABASE_NODES[@]}"; do
                node_fqdn="${node}.${DOMAIN}"
                echo "[$(date)] Updating config on $node_fqdn..." >> "$LOGFILE"

                SSH_NODE="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
-o StrictHostKeyChecking=no $USERNAME@$node_fqdn"

                $SSH_NODE sudo sed -E -i "s/(host=)[^[:space:]]+/\1${NEW_MASTER_HOST}/" "$NEW_MASTER_DATA/postgresql.auto.conf"

                if [ "$node_fqdn" != "$OLD_MASTER_HOST" ] && [ "$node_fqdn" != "$NEW_MASTER_HOST" ]; then
                    $SSH_NODE "sudo systemctl restart postgresql@11-contrprimer.service"
                fi
            done
        fi
        ;;
    BACK)
        echo "[$(date)] Performing failback: making $OLD_MASTER_HOST a replica of $NEW_MASTER_HOST..." >> "$LOGFILE"

        $SSH_OLD "sudo systemctl stop postgresql@11-contrprimer.service"
        $SSH_OLD "sudo rm -rf /var/lib/postgresql/11/contrprimer/*"

        $SSH_OLD "PGPASSWORD=1 pg_basebackup -h $NEW_MASTER_HOST -p $POSTGRES_PORT -U postgres \
-D /var/lib/postgresql/11/contrprimer -Fp -Xs -P -R"

        $SSH_OLD "sudo systemctl start postgresql@11-contrprimer.service"

        echo "[$(date)] Failback complete: $OLD_MASTER_HOST is now a replica." >> "$LOGFILE"
        ;;
    *)
        echo "Invalid mode: $MODE. Use OVER or BACK." >> "$LOGFILE"
        exit 1
        ;;
esac

echo ">>> [$MODE] DONE"
exit 0
