#!/bin/bash

# Путь к файлу с переменными
ENV_FILE="/home/u/devpi-service/.env"

# Значения по умолчанию
DEFAULT_ROOT_PASS="root"
DEFAULT_TEST_USER="user"
DEFAULT_TEST_PASS="user"

SERVICE_NAME="devpi.service"
PROJECT_PATH="/home/u/devpi-service" # TODO изменить

if [ -f "$ENV_FILE" ]; then
    echo "Загрузка переменных из $ENV_FILE..."
    # Экспортируем все переменные из файла .env
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "Предупреждение: файл $ENV_FILE не найден! Используются значения по умолчанию."
    DEVPI_ADMIN_PASSWORD="$DEFAULT_ROOT_PASS"
    DEVPI_USER="$DEFAULT_TEST_USER"
    DEVPI_PASSWORD="$DEFAULT_TEST_PASS"
fi

cp Dockerfile.default Dockerfile
# Проверяем наличие Dockerfile
if [ ! -f Dockerfile ]; then
    echo "Ошибка: Dockerfile не найден!"
    exit 1
fi

echo "Обновление Dockerfile с использованием переменных..."

# Заменяем зашитые значения на переменные
sed -i "s/pass_adm/${DEVPI_ADMIN_PASSWORD}/g" Dockerfile
sed -i "s/test_n/${DEVPI_USER}/g" Dockerfile
sed -i "s/test_p/${DEVPI_PASSWORD}/g" Dockerfile
echo "Dockerfile обновлен."

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

        sudo systemctl enable "$SERVICE_NAME"
        sudo systemctl daemon-reload
        sudo systemctl start "$SERVICE_NAME"
        sudo systemctl status "$SERVICE_NAME"
    else
        echo "Сервис уже запущен."
    fi
}

# Запуск установки сервиса
install
