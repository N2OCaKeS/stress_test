#!/bin/bash
set -euxo pipefail

#
# follow.sh — синхронизация старого узла под нового primary
#
# Аргументы от pgpool-II:
#   $1  NODE_ID
#   $2  NODE_HOST
#   $3  NODE_PORT
#   $4  NODE_PGDATA
#   $5  NEW_PRIMARY_NODE_ID
#   $6  NEW_PRIMARY_NODE_HOST
#   $7  OLD_MAIN_NODE_ID        — не используется
#   $8  OLD_PRIMARY_NODE_ID     — не используется
#   $9  NEW_PRIMARY_NODE_PORT
#  $10  NEW_PRIMARY_NODE_PGDATA — не используется
#

NODE_ID="$1"
NODE_HOST="$2"
NODE_PORT="$3"
NODE_PGDATA="$4"
NEW_ID="$5"
NEW_HOST="$6"
NEW_PORT="$9"

# Определяем версию Astra/PG
OS_VERSION=$(cut -d. -f1,2 /etc/astra_version)
if   [ "$OS_VERSION" == "1.7" ]; then VERSION_PG=11
elif [ "$OS_VERSION" == "1.8" ]; then VERSION_PG=15
else
  echo "Unsupported Astra Linux version: $OS_VERSION"
  exit 1
fi

# Константы
USERNAME="u"
PASSWORD="1"
REPLUSER="postgres"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
PG_BIN="/usr/lib/postgresql/${VERSION_PG}/bin"
ARCHIVE_DIR="/var/lib/postgresql/${VERSION_PG}/archivedir"
PCP_HOST="pgpool.balance.rbt"
PCP_USER="pgpool"
PCP_PASSFILE="/tmp/.pcppass"

echo "=== follow.sh start NODE ${NODE_ID} (${NODE_HOST}:${NODE_PORT}) ==="

#
# 1) Ждём, пока новый primary готов отдавать WAL
#
echo "Waiting until new primary ${NEW_HOST}:${NEW_PORT} is ready..."
until pg_isready -h "${NEW_HOST}" -p "${NEW_PORT}" >/dev/null 2>&1; do
  sleep 1
done

#
# 2) Останавливаем старый узел, ждём завершения и убираем postmaster.pid
#
echo "Stopping PostgreSQL on ${NODE_HOST}..."
sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo bash <<EOF
set -eux
systemctl stop postgresql@${VERSION_PG}-contrprimer
while systemctl is-active --quiet postgresql@${VERSION_PG}-contrprimer; do
  sleep 1
done
rm -f "${NODE_PGDATA}/postmaster.pid"
EOF

#
# 3) Пробуем pg_rewind
#
# echo "Attempting pg_rewind on ${NODE_HOST}..."
# sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo bash <<EOF
# set -eux
# sudo su - postgres -c "\
#   '${PG_BIN}/pg_rewind' \
#     --target-pgdata='${NODE_PGDATA}' \
#     --source-server='host=${NEW_HOST} port=${NEW_PORT} user=${REPLUSER}'"
# EOF

#
# 4) Если pg_rewind вернул не 0, чистим data/archivedir и делаем pg_basebackup -R
#
# if [ $? -ne 0 ]; then
echo "cleaning data dir and running pg_basebackup -R..."
sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo bash <<EOF
set -eux
rm -rf "${NODE_PGDATA:?}/"* "${ARCHIVE_DIR:?}/"*
sudo su - postgres -c "\
  '${PG_BIN}/pg_basebackup' \
    -h '${NEW_HOST}' -p '${NEW_PORT}' \
    -U '${REPLUSER}' \
    -D '${NODE_PGDATA}' \
    -R -X stream -P"
EOF
# fi

#
# 5) Генерируем «чистый» recovery/standby конфиг под нужную версию
#
echo "Configuring recovery/standby for PG${VERSION_PG} on ${NODE_HOST}..."
if [ "${VERSION_PG}" -ge 12 ]; then
  # — PG12+: touch standby.signal и дописываем в postgresql.conf
  sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo bash <<EOF
set -eux
touch "${NODE_PGDATA}/standby.signal"
sed -i "/^primary_conninfo/d" /etc/postgresql/${VERSION_PG}/contrprimer/postgresql.conf
EOF

  sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo tee -a /etc/postgresql/${VERSION_PG}/contrprimer/postgresql.conf <<EOF
# managed by follow.sh
primary_conninfo = 'host=${NEW_HOST} port=${NEW_PORT} user=${REPLUSER}'
EOF

else
  # — PG11: создаём recovery.conf
  sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo rm -rf "${NODE_PGDATA}/recovery.conf" 
  sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo rm -rf "${ARCHIVE_DIR:?}/"*
  sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo touch "${NODE_PGDATA}/recovery.conf"
  sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo tee "${NODE_PGDATA}/recovery.conf" <<EOF
standby_mode = 'on'
primary_conninfo = 'user=${REPLUSER} passfile=''/var/lib/postgresql/.pgpass'' host=${NEW_HOST} port=${NEW_PORT} sslmode=prefer sslcompression=0 krbsrvname=postgres target_session_attrs=any'
recovery_target_timeline = 'latest'
EOF
fi

#
# 6) Запускаем PostgreSQL на старом узле как реплику и ждём готовности
#
echo "Starting PostgreSQL on ${NODE_HOST}..."
sshpass -p "${PASSWORD}" ssh ${SSH_OPTS} ${USERNAME}@${NODE_HOST} sudo bash <<EOF
  set -eux
  systemctl start postgresql@${VERSION_PG}-contrprimer

  # 6.1) Ждём, пока systemd отметит сервис как active
  until systemctl is-active --quiet postgresql@${VERSION_PG}-contrprimer; do
    echo "  ↻ waiting for systemd to mark postgresql@${VERSION_PG}-contrprimer active..."
    sleep 1
  done

  # 6.2) Ждём, пока Postgres начнёт принимать соединения
  until pg_isready -h 127.0.0.1 -p "${NODE_PORT}" >/dev/null 2>&1; do
    echo "  ↻ waiting for Postgres to accept connections on port ${NODE_PORT}..."
    sleep 1
  done

  echo "  ✓ PostgreSQL is up and ready"
EOF

#
# 7) Подключаем узел обратно в кластер через PCP
#
echo "Attaching node ${NODE_ID} to Pgpool..."
sudo PCPPASSFILE="${PCP_PASSFILE}" \
  pcp_attach_node -h "${PCP_HOST}" -U "${PCP_USER}" -n "${NODE_ID}" -w

echo "=== follow.sh done for NODE ${NODE_ID} ==="
exit 0
