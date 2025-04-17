#!/bin/bash
# FAILOVER / FAILBACK скрипт для Pgpool-II

set -euo pipefail

# Перейти в безопасную директорию, чтобы избежать ошибок доступа
cd /tmp || exit 1

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

# SSH‑опции
SSH_OPTS="-T \
  -o ControlMaster=auto \
  -o ControlPersist=2m \
  -o GlobalKnownHostsFile=/dev/null \
  -o UserKnownHostsFile=/dev/null \
  -o StrictHostKeyChecking=no"
SSHPASS="sshpass -p $PASSWORD"
SSH_NEW="$SSHPASS ssh $SSH_OPTS $USERNAME@$NEW_MASTER_HOST"
SSH_OLD="$SSHPASS ssh $SSH_OPTS $USERNAME@$OLD_MASTER_HOST"

# Логируем начало
echo "[$(date)] MODE: $MODE | NEW_MASTER: $NEW_MASTER_HOST | FAILED: $FAILED_HOST | OLD_MASTER: $OLD_MASTER_HOST" >> "$LOGFILE"
echo ">>> [$MODE] START"

case "$MODE" in
  OVER)
    if [ "$OLD_MASTER_ID" -ne "$NEW_MASTER_ID" ] && [ "$NEW_MASTER_ID" -ge 0 ]; then
      echo "[$(date)] Promoting $NEW_MASTER_HOST to primary..." >> "$LOGFILE"
      # Продвижение нового мастера и ожидание завершения
      $SSH_NEW "cd /tmp && \
        sudo -u postgres /usr/lib/postgresql/11/bin/pg_ctl -D ${NEW_MASTER_DATA} promote && \
        until ! sudo -u postgres psql -h localhost -p $POSTGRES_PORT -U postgres -tAc \"SELECT pg_is_in_recovery()\" | grep -q t; do sleep 1; done"

      echo "[$(date)] Promotion complete, updating replicas..." >> "$LOGFILE"
      # Обновляем recovery.conf и перезапускаем реплики
      for node in "${DATABASE_NODES[@]}"; do
        node_fqdn="${node}.${DOMAIN}"
        if [ "$node_fqdn" != "$NEW_MASTER_HOST" ] && [ "$node_fqdn" != "$OLD_MASTER_HOST" ]; then
          echo "[$(date)] Updating recovery.conf on $node_fqdn..." >> "$LOGFILE"
          SSH_NODE="$SSHPASS ssh $SSH_OPTS $USERNAME@$node_fqdn"
          $SSH_NODE "cd /tmp && \
            sudo sed -E -i 's|(host=)[^[:space:]]+|\1${NEW_MASTER_HOST}|' ${NEW_MASTER_DATA}/recovery.conf && \
            sudo systemctl restart postgresql@11-contrprimer.service"
        fi
      done
    else
      echo "[$(date)] No promotion needed (OLD_MASTER_ID == NEW_MASTER_ID or invalid NEW_MASTER_ID)" >> "$LOGFILE"
    fi
    ;;
  BACK)
    echo "[$(date)] Performing failback: making $OLD_MASTER_HOST a replica of $NEW_MASTER_HOST..." >> "$LOGFILE"
    $SSH_OLD "cd /tmp && \
      sudo systemctl stop postgresql@11-contrprimer.service && \
      sudo rm -rf /var/lib/postgresql/11/contrprimer/* && \
      PGPASSWORD=$PASSWORD pg_basebackup -h $NEW_MASTER_HOST -p $POSTGRES_PORT -U postgres \
        -D /var/lib/postgresql/11/contrprimer -Fp -Xs -P -R && \
      sudo systemctl start postgresql@11-contrprimer.service"
    echo "[$(date)] Failback complete: $OLD_MASTER_HOST is now a replica." >> "$LOGFILE"
    ;;
  *)
    echo "Invalid mode: $MODE. Use OVER or BACK." >> "$LOGFILE"
    exit 1
    ;;
esac

echo ">>> [$MODE] DONE"
exit 0
