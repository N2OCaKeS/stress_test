#!/bin/sh
set -e

# Проверяем, что нужные переменные заданы
: "${DEVPI_ADMIN_PASSWORD:?Не задана переменная DEVPI_ADMIN_PASSWORD}"
: "${DEVPI_USER:?Не задана переменная DEVPI_USER}"
: "${DEVPI_PASSWORD:?Не задана переменная DEVPI_PASSWORD}"

# если папка пуста — инициализируем с паролем из переменной
if [ -z "$(ls -A /data)" ]; then
  echo "First run: initializing devpi-server data…"
  devpi-init --serverdir /data --root-passwd=$DEVPI_ADMIN_PASSWORD
fi

# 1) Запускаем devpi-server в фоне
devpi-server \
  --host 0.0.0.0 \
  --port 3141 \
  --serverdir /data &

DEVPI_PID=$!

# 2) Ждём, пока devpi поднимется
until nc -z localhost 3141; do
  echo "waiting for devpi-server..."
  sleep 1
done

# 3) Конфигурируем devpi: логинимся под root и создаём индексы/пользователя
DEVPI_SERVER_URL="http://localhost:3141"
devpi use "$DEVPI_SERVER_URL" && \
      devpi login root --password "$DEVPI_ADMIN_PASSWORD" && \
      devpi index -y -c release bases=root/pypi mirror_whitelist=\* && \
      devpi user -y -c "$DEVPI_USER" password="$DEVPI_PASSWORD" && \
      devpi user -m root password="$DEVPI_ADMIN_PASSWORD" && \
      devpi login "$DEVPI_USER" --password "$DEVPI_PASSWORD" && \
      devpi index -y -c dev bases=root/pypi mirror_whitelist=\*

# kill "$DEVPI_PID"
# # 3) Запускаем devpi-server в фоне
# devpi-server \
#   --host 0.0.0.0 \
#   --port 3141 \
#   --serverdir /data 
#   --outside-url http://allta.devos.astralinux.ru/devpi &
# DEVPI_PID=$!

# # 4) Ждём, пока devpi поднимется
# until nc -z localhost 3141; do
#   echo "waiting for devpi-server..."
#   sleep 1
# done

# # 5) Запускаем ваш скрипт new_release.py в фоне
# nohup python /git/new_release.py \
#     >> /data/new_release.log 2>&1 &

# 6) Ждём завершения devpi-server (чтобы контейнер не остановился)
python /git/new_release.py
