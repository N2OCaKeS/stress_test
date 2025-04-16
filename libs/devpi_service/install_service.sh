#!/bin/bash

# Путь к файлу с переменными
ENV_FILE="/home/u/devpi-service/stress_test/libs/devpi_service/.env"

# Значения по умолчанию
DEFAULT_ROOT_PASS="root"
DEFAULT_TEST_USER="user"
DEFAULT_TEST_PASS="user"

SERVICE_NAME="devpi.service"
PROJECT_PATH="/home/u/devpi-service/stress_test/libs/devpi_service/" 

if [ -f "$ENV_FILE" ]; then
    echo "Загрузка переменных из $ENV_FILE..."
    # Экспортируем все переменные из файла .env
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "Предупреждение: файл $ENV_FILE не найден! Создание со значениями по умолчанию..."
    
    DEVPI_ADMIN_PASSWORD="$DEFAULT_ROOT_PASS"
    DEVPI_USER="$DEFAULT_TEST_USER"
    DEVPI_PASSWORD="$DEFAULT_TEST_PASS"

    cat << EOF > "$ENV_FILE"
DEVPI_USER=$DEVPI_USER
DEVPI_PASSWORD=$DEVPI_PASSWORD
DEVPI_ADMIN_PASSWORD=$DEVPI_ADMIN_PASSWORD
EOF

    echo "$ENV_FILE создан со значениями по умолчанию."
fi

cp $PROJECT_PATH/Dockerfile.default $PROJECT_PATH/Dockerfile
# Проверяем наличие Dockerfile
if [ ! -f $PROJECT_PATH/Dockerfile ]; then
    echo "Ошибка: Dockerfile не найден!"
    exit 1
fi

echo "Обновление Dockerfile с использованием переменных..."

# Заменяем зашитые значения на переменные
sed -i "s/pass_adm/${DEVPI_ADMIN_PASSWORD}/g" $PROJECT_PATH/Dockerfile
sed -i "s/test_n/${DEVPI_USER}/g" $PROJECT_PATH/Dockerfile
sed -i "s/test_p/${DEVPI_PASSWORD}/g" $PROJECT_PATH/Dockerfile
echo "$PROJECT_PATH/Dockerfile обновлен."

# Функция для установки и запуска сервиса
install() {
    dpkg -s docker || sudo apt-get install docker.io -y
    dpkg -s docker-compose || sudo apt-get install docker-compose -y

    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        sudo tee /etc/systemd/system/"$SERVICE_NAME" > /dev/null << EOF
[Unit]
Description=Docker DevPi Service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_PATH
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF



    sudo systemctl daemon-reexec
    sudo systemctl daemon-reload
    sudo systemctl enable devpi.service
    sudo systemctl restart devpi.service
    sudo systemctl status devpi.service

    else
        echo "Сервис уже запущен."
    fi
}

# Запуск установки сервиса
install
