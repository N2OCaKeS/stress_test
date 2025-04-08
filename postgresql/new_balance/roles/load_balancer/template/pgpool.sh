#!/bin/bash
# Скрипт: cluster_failover.sh
# Использование:
#   cluster_failover.sh failover   - для запуска процедуры failover (мастер недоступен)
#   cluster_failover.sh recovery   - для восстановления оригинального мастера и свитчовера

# --- Настройте переменные под вашу инфраструктуру ---
OLD_MASTER_IP="192.168.1.101"      # IP оригинального мастера
REPLICA1_IP="192.168.1.102"        # IP первой реплики (предполагаем кандидат на failover)
REPLICA2_IP="192.168.1.103"        # IP второй реплики (можно добавить логику выбора, если нужно)

# Пути и настройки PostgreSQL
DATA_DIR="/var/lib/pgsql/data"
REPLICATION_USER="postgres"
REPLICATION_PASS="yourpassword"

# По умолчанию выбираем первую реплику для повышения
CANDIDATE_IP="${REPLICA1_IP}"

# Функция для выполнения failover: повышение реплики в мастера
failover() {
    echo "=== FAILOVER: Промоция реплики $CANDIDATE_IP в роль мастера ==="
    # На целевом узле выполняем промоцию через pg_ctl
    ssh u@"$CANDIDATE_IP" "sudo su postgres -c 'pg_ctl promote -D ${DATA_DIR}'"
    if [ $? -eq 0 ]; then
        echo "Реплика $CANDIDATE_IP успешно промотирована в мастера."
    else
        echo "Ошибка при промоции реплики $CANDIDATE_IP."
        exit 1
    fi

    # Здесь можно добавить обновление конфигурации pgpool или уведомление админа
    echo "Обновите настройки Pgpool-II, чтобы новый мастер ($CANDIDATE_IP) принимал подключения."
}

# Функция для восстановления оригинального мастера и выполнения свитчовера
recovery() {
    echo "=== RECOVERY: Восстановление оригинального мастера $OLD_MASTER_IP ==="

    # Остановка PostgreSQL на оригинальном мастере
    echo "Остановка PostgreSQL на $OLD_MASTER_IP..."
    ssh u@"$OLD_MASTER_IP" "sudo systemctl stop postgresql"
    
    # Переименование старой директории данных на оригинальном мастере
    echo "Переименование директории данных на $OLD_MASTER_IP..."
    ssh u@"$OLD_MASTER_IP" "sudo mv ${DATA_DIR} ${DATA_DIR}_old_$(date +%F_%T)"

    # Восстановление данных с текущего мастера (промотированной реплики)
    echo "Выполнение pg_basebackup с $CANDIDATE_IP на $OLD_MASTER_IP..."
    ssh u@"$OLD_MASTER_IP" "sudo su - postgres -c 'pg_basebackup -h ${CANDIDATE_IP} -p 5440 -D ${DATA_DIR} -U ${REPLICATION_USER} -P --wal-method=stream'"
    if [ $? -ne 0 ]; then
        echo "Ошибка при выполнении pg_basebackup на $OLD_MASTER_IP."
        exit 1
    fi

    # Для PostgreSQL 12+ создаём standby сигнал
    ssh postgres@"$OLD_MASTER_IP" "touch ${DATA_DIR}/standby.signal"
    
    # Обновление primary_conninfo на оригинальном мастере
    ssh u@"$OLD_MASTER_IP" "sudo su root -c 'echo \"primary_conninfo = 'host=${CANDIDATE_IP} port=5432 user=${REPLICATION_USER} password=${REPLICATION_PASS}'\" >> ${DATA_DIR}/postgresql.auto.conf'"

    # Запуск PostgreSQL на оригинальном мастере в режиме реплики
    echo "Запуск PostgreSQL на $OLD_MASTER_IP в режиме реплики..."
    ssh u@"$OLD_MASTER_IP" "sudo systemctl start postgresql"

    echo "Оригинальный мастер синхронизирован как реплика."
    echo "=== SWITCHOVER: Возвращаем оригинальный мастер в роль мастера ==="

    # Останавливаем текущего мастера (раньше промотированную реплику)
    echo "Остановка текущего мастера (бывшая реплика) на $CANDIDATE_IP..."
    ssh u@"$CANDIDATE_IP" "sudo systemctl stop postgresql"

    # Если на оригинальном мастере имеется standby.signal, удаляем его для промоции
    ssh u@"$OLD_MASTER_IP" "sudo rm -f ${DATA_DIR}/standby.signal"
    
    # Промоция оригинального мастера
    echo "Промоция оригинального мастера $OLD_MASTER_IP..."
    ssh u@"$OLD_MASTER_IP" "sudo su - postgres -c 'pg_ctl promote -D ${DATA_DIR}'"
    if [ $? -eq 0 ]; then
        echo "Оригинальный мастер $OLD_MASTER_IP успешно промотирован."
    else
        echo "Ошибка при промоции оригинального мастера $OLD_MASTER_IP."
        exit 1
    fi

    # Переконфигурируем бывшего мастера (теперь $CANDIDATE_IP) в режим реплики:
    echo "Переустановка $CANDIDATE_IP в режим реплики..."
    ssh postgres@"$CANDIDATE_IP" "mv ${DATA_DIR} ${DATA_DIR}_old_$(date +%F_%T)"
    ssh postgres@"$CANDIDATE_IP" "pg_basebackup -h ${OLD_MASTER_IP} -D ${DATA_DIR} -U ${REPLICATION_USER} -P --wal-method=stream"
    ssh postgres@"$CANDIDATE_IP" "touch ${DATA_DIR}/standby.signal"
    ssh postgres@"$CANDIDATE_IP" "echo \"primary_conninfo = 'host=${OLD_MASTER_IP} port=5432 user=${REPLICATION_USER} password=${REPLICATION_PASS}'\" >> ${DATA_DIR}/postgresql.auto.conf"
    ssh postgres@"$CANDIDATE_IP" "systemctl start postgresql"
    echo "Бывший мастер $CANDIDATE_IP теперь успешно настроен как реплика."
}

# Основной блок обработки параметров
case "$1" in
    failover)
        failover
        ;;
    recovery)
        recovery
        ;;
    *)
        echo "Использование: $0 {failover|recovery}"
        exit 1
        ;;
esac
