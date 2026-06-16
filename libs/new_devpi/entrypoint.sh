#!/bin/sh
# Инициализация devpi в рантайме. Никаких хардкодов из Dockerfile —
# всё берётся из переменных окружения (env.devpi), с разумными дефолтами.
set -eu

SERVERDIR="${DEVPI_SERVERDIR:-/data}"
PORT="${DEVPI_PORT:-3141}"
HOST="${DEVPI_HOST:-0.0.0.0}"

# Пароль root обязателен — без него сервер не настроить.
: "${DEVPI_ROOT_PASSWORD:?нужно задать DEVPI_ROOT_PASSWORD в env.devpi}"

RELEASE_INDEX="${DEVPI_RELEASE_INDEX:-release}"
DEV_INDEX="${DEVPI_DEV_INDEX:-dev}"
INDEX_BASES="${DEVPI_INDEX_BASES:-root/pypi}"

# devpi-клиент сам авторизуется из переменных DEVPI_USER/DEVPI_PASSWORD, если они
# есть в окружении, и перебивает 'devpi login root' — из-за этого падали index -c
# под root. Сохраняем значения для создания пользователя и убираем из окружения
# (заодно защищаем дочерний new_release.py, который работает под root).
DEVPI_USER_VALUE="${DEVPI_USER:-}"
DEVPI_PASSWORD_VALUE="${DEVPI_PASSWORD:-}"
unset DEVPI_USER DEVPI_PASSWORD

export DEVPISERVER_SERVERDIR="$SERVERDIR"

# Свежий клиентский каталог на каждый запуск: токены из прошлого запуска после
# рестарта уже невалидны и иначе ломают devpi index -c ("not logged in").
DEVPI_CLIENTDIR="$(mktemp -d)"
export DEVPI_CLIENTDIR

# Первый запуск: инициализируем серверную директорию с паролем root.
if [ ! -f "$SERVERDIR/.serverversion" ]; then
    echo "Первичная инициализация devpi в $SERVERDIR"
    devpi-init --serverdir "$SERVERDIR" --root-passwd "$DEVPI_ROOT_PASSWORD"
fi

# Постоянный секрет: без него каждый рестарт генерит новый случайный секрет и
# инвалидирует токены логина — из-за этого падал devpi index -c.
SECRET_FILE="$SERVERDIR/.devpi-secret"
if [ ! -s "$SECRET_FILE" ]; then
    echo "Создаю постоянный секрет: $SECRET_FILE"
    devpi-gen-secret --secretfile "$SECRET_FILE"
fi

# Поднимаем сервер в фоне, держим его PID — на нём контейнер и будет жить.
devpi-server --host "$HOST" --port "$PORT" --serverdir "$SERVERDIR" --secretfile "$SECRET_FILE" &
SERVER_PID=$!

# Ждём, пока сервер начнёт отвечать (не дольше 60 секунд).
ready=0
i=0
while [ "$i" -lt 60 ]; do
    if devpi use "http://localhost:$PORT" >/dev/null 2>&1; then
        ready=1
        break
    fi
    i=$((i + 1))
    sleep 1
done

if [ "$ready" -ne 1 ]; then
    echo "devpi-server не поднялся за 60 секунд, прерываю запуск." >&2
    kill "$SERVER_PID" 2>/dev/null || true
    exit 1
fi

# Логинимся как root для дальнейшей настройки индексов и пользователей.
devpi login root --password "$DEVPI_ROOT_PASSWORD"

# Релизный индекс создаём только если его ещё нет. Проверяем через index -l,
# чтобы не сбивать контекст клиента запросом несуществующего индекса.
if ! devpi index -l 2>/dev/null | grep -qx "root/$RELEASE_INDEX"; then
    echo "Создаю индекс root/$RELEASE_INDEX"
    devpi index -c "root/$RELEASE_INDEX" bases="$INDEX_BASES" mirror_whitelist='*'
fi

# Опциональный пользователь и его dev-индекс.
if [ -n "$DEVPI_USER_VALUE" ] && [ -n "$DEVPI_PASSWORD_VALUE" ]; then
    devpi user -c "$DEVPI_USER_VALUE" password="$DEVPI_PASSWORD_VALUE" || true
    devpi login "$DEVPI_USER_VALUE" --password "$DEVPI_PASSWORD_VALUE"
    if ! devpi index -l 2>/dev/null | grep -qx "$DEVPI_USER_VALUE/$DEV_INDEX"; then
        echo "Создаю индекс $DEVPI_USER_VALUE/$DEV_INDEX"
        devpi index -c "$DEVPI_USER_VALUE/$DEV_INDEX" bases="$INDEX_BASES" mirror_whitelist='*'
    fi
    # Возвращаемся под root, чтобы new_release.py работал с релизным индексом.
    devpi login root --password "$DEVPI_ROOT_PASSWORD"
fi

# Запускаем мониторинг релизов. Падение скрипта не должно ронять контейнер.
python3 /git/new_release.py || echo "new_release.py завершился с кодом: $?"

# Держим контейнер на переднем плане самим сервером.
wait "$SERVER_PID"
