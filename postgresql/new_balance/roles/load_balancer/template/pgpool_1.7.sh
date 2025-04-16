#!/bin/bash
# FAILOVER / FAILBACK скрипт для PGpool
#
# Использование:
#   ./failover.sh <MODE> <NEW_MASTER_ID> <NEW_MASTER_HOST> <NEW_MASTER_DATA> <FAILED_NODE_ID> <FAILED_HOST> <OLD_MASTER_ID> <OLD_MASTER_HOST>
#
# Параметр MODE:
#   OVER  - продвигаем новую ноду в мастера (failover)
#   BACK  - возвращаем старый мастер в качестве мастера (failback)
#
# Эти параметры передаются PGpool:
#   NEW_MASTER_ID    - ID ноды, которая становится мастером (в failover — новая нода, в failback — старая)
#   NEW_MASTER_HOST  - FQDN новой ноды
#   NEW_MASTER_DATA  - Путь к каталогу данных PostgreSQL на ноде (используется для обновления конфигурационных файлов)
#   FAILED_NODE_ID   - ID ноды, потерявшей связь
#   FAILED_HOST      - FQDN ноды, потерявшей связь
#   OLD_MASTER_ID    - ID старого мастера
#   OLD_MASTER_HOST  - FQDN старого мастера

# Проверка количества аргументов (с учётом параметра MODE)
if [ "$#" -ne 8 ]; then
    echo "Usage: $0 <MODE: OVER|BACK> <NEW_MASTER_ID> <NEW_MASTER_HOST> <NEW_MASTER_DATA> <FAILED_NODE_ID> <FAILED_HOST> <OLD_MASTER_ID> <OLD_MASTER_HOST>"
    exit 1
fi

# Параметры, полученные от PGpool/вызывающего процесса
MODE=$1
NEW_MASTER_ID=$2
NEW_MASTER_HOST=$3
NEW_MASTER_DATA=$4
FAILED_NODE_ID=$5
FAILED_HOST=$6
OLD_MASTER_ID=$7
OLD_MASTER_HOST=$8

# Жёстко заданные настройки
USERNAME="u"
PASSWORD="1"
POSTGRES_PORT=5440
DOMAIN="balance.rbt"

# Список нод базы данных (из группы "database")
DATABASE_NODES=("database1" "database2" "database3")

# Подготовка SSH-команды с использованием sshpass
SSHPASS="sshpass -p $PASSWORD"

# SSH для новой ноды
SSH_NEW="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
-o StrictHostKeyChecking=no $USERNAME@$NEW_MASTER_HOST"

# SSH для старой ноды
SSH_OLD="$SSHPASS ssh -T -o ControlMaster=auto -o ControlPersist=2m \
-o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=/dev/null \
-o StrictHostKeyChecking=no $USERNAME@$OLD_MASTER_HOST"

echo "!!! FAILOVER/FAILBACK START !!!"
echo "Параметры, полученные от PGpool:"
echo "  Новый мастер: $NEW_MASTER_HOST (ID: $NEW_MASTER_ID, Data: $NEW_MASTER_DATA)"
echo "  Провалившаяся нода: $FAILED_HOST (ID: $FAILED_NODE_ID)"
echo "  Старый мастер: $OLD_MASTER_HOST (ID: $OLD_MASTER_ID)"
echo "  Режим: $MODE"

# Переменная MASTER_HOST будет содержать FQDN, который надо прописать в конфиге
case "$MODE" in
    OVER)
# Если новый мастер отличается от старого и его ID больше или равен 0, проводим failover
        if [ "$OLD_MASTER_ID" -ne "$NEW_MASTER_ID" ] && [ "$NEW_MASTER_ID" -ge 0 ]; then
            echo "Начинается продвижение новой ноды в мастер..."
            $SSH /usr/lib/postgresql/11/bin/pg_ctl promote -D /var/lib/postgresql/11/contrprimer/


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
                $SSH_NODE sudo sed -E -i "s/(host=)[^[:space:]]+/\1${NEW_MASTER_HOST}/" "$NEW_MASTER_DATA/postgresql.auto.conf"
                # Если нода не является ни старым, ни новым мастером, перезагружаем конфигурацию PostgreSQL
                if [ "$node_fqdn" != "$OLD_MASTER_HOST" ] && [ "$node_fqdn" != "$NEW_MASTER_HOST" ]; then
                    $SSH_NODE "sudo systemctl restart postgresql@11-contrprimer.service"
                fi
            done
        fi
        ;;
    BACK)
        echo "Начинается failback: старый мастер ($OLD_MASTER_HOST) станет репликой нового мастера ($NEW_MASTER_HOST)..."

        # 1. Останавливаем PostgreSQL на старом мастере
        $SSH_OLD "sudo systemctl stop postgresql@11-contrprimer.service"

        # 2. Очищаем старые данные
        $SSH_OLD "sudo rm -rf /var/lib/postgresql/11/contrprimer/*"

        # 3. Копируем данные с текущего мастера
        $SSH_OLD "pg_basebackup -h $NEW_MASTER_HOST -p $POSTGRES_PORT -U postgres \
                  -D /var/lib/postgresql/11/contrprimer -Fp -Xs -P -R"

        # 4. Запускаем PostgreSQL (он уже будет в режиме реплики благодаря -R)
        $SSH_OLD "sudo systemctl start postgresql@11-contrprimer.service"

        echo "Старый мастер теперь настроен как реплика нового мастера"
        ;;
    *)
        echo "Неверный режим. Используйте OVER или BACK."
        exit 1
        ;;
esac

echo "!!! FAILOVER/FAILBACK FINISH !!!"
exit 0
