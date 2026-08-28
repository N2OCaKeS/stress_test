#!/bin/sh
# Инициализация devpi в рантайме. Никаких хардкодов из Dockerfile —
# всё берётся из переменных окружения (env.devpi), с разумными дефолтами.
set -eu

SERVERDIR="${DEVPI_SERVERDIR:-/data}"
PORT="${DEVPI_PORT:-3141}"
HOST="${DEVPI_HOST:-0.0.0.0}"

# Пароль root обязателен — без него сервер не настроить.
: "${DEVPI_ROOT_PASSWORD:?нужно задать DEVPI_ROOT_PASSWORD в env.devpi}"

PYPI_INDEX="${DEVPI_PYPI_INDEX:-pypi}"
RELEASE_INDEX="${DEVPI_RELEASE_INDEX:-release}"
TEST_INDEX="${DEVPI_TEST_INDEX:-test}"
LOCAL_UPLOAD_USER="${DEVPI_LOCAL_UPLOAD_USER:-allta}"
LOCAL_UPLOAD_PASSWORD="${DEVPI_LOCAL_UPLOAD_PASSWORD:-}"
PYPI_INDEX_BASES="${DEVPI_PYPI_INDEX_BASES:-}"
RELEASE_INDEX_BASES="${DEVPI_RELEASE_INDEX_BASES-root/$PYPI_INDEX}"
TEST_INDEX_BASES="${DEVPI_TEST_INDEX_BASES-root/$RELEASE_INDEX}"
PYPI_VOLATILE="${DEVPI_PYPI_VOLATILE:-False}"
RELEASE_VOLATILE="${DEVPI_RELEASE_VOLATILE:-False}"
TEST_VOLATILE="${DEVPI_TEST_VOLATILE:-True}"
PYPI_ACL_UPLOAD="${DEVPI_PYPI_ACL_UPLOAD:-$LOCAL_UPLOAD_USER}"
ACL_UPLOAD="${DEVPI_ACL_UPLOAD:-root,$LOCAL_UPLOAD_USER,:devpi_upload}"
DISABLE_ROOT_PYPI="${DEVPI_DISABLE_ROOT_PYPI:-1}"
RESTRICT_MODIFY="${DEVPI_RESTRICT_MODIFY:-root}"
AUTOCREATE_USERS="${DEVPI_AUTOCREATE_USERS:-1}"

if [ -n "$LOCAL_UPLOAD_USER" ] && [ -z "$LOCAL_UPLOAD_PASSWORD" ]; then
    echo "нужно задать DEVPI_LOCAL_UPLOAD_PASSWORD для локального пользователя $LOCAL_UPLOAD_USER" >&2
    exit 1
fi

# devpi-клиент сам авторизуется из переменных DEVPI_USER/DEVPI_PASSWORD, если они
# есть в окружении, и перебивает 'devpi login root'. Пользователи теперь
# приходят из Allta Auth API, поэтому локальные DEVPI_USER/DEVPI_PASSWORD не нужны.
unset DEVPI_USER DEVPI_PASSWORD

export DEVPISERVER_SERVERDIR="$SERVERDIR"

# Свежий клиентский каталог на каждый запуск: токены из прошлого запуска после
# рестарта уже невалидны и иначе ломают devpi index -c ("not logged in").
DEVPI_CLIENTDIR="$(mktemp -d)"
export DEVPI_CLIENTDIR

is_truthy() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

NO_ROOT_PYPI_ARG=""
if is_truthy "$DISABLE_ROOT_PYPI"; then
    NO_ROOT_PYPI_ARG="--no-root-pypi"
fi

RESTRICT_MODIFY_ARG=""
if [ -n "$RESTRICT_MODIFY" ]; then
    RESTRICT_MODIFY_ARG="--restrict-modify=$RESTRICT_MODIFY"
fi

AUTOCREATE_USERS_ARG=""
if is_truthy "$AUTOCREATE_USERS"; then
    AUTOCREATE_USERS_ARG="--autocreate-users"
fi


index_exists() {
    devpi index -l 2>/dev/null | grep -qx "$1"
}

configure_index() {
    index_name="$1"
    bases="$2"
    volatile="$3"
    acl="$4"

    if index_exists "$index_name"; then
        echo "Настраиваю индекс $index_name"
        devpi index "$index_name" "bases=$bases" "volatile=$volatile" "acl_upload=$acl" mirror_whitelist=
    else
        echo "Создаю индекс $index_name"
        devpi index -c "$index_name" "bases=$bases" "volatile=$volatile" "acl_upload=$acl" mirror_whitelist=
    fi
}

# Первый запуск: инициализируем серверную директорию с паролем root.
if [ ! -f "$SERVERDIR/.serverversion" ]; then
    echo "Первичная инициализация devpi в $SERVERDIR"
    devpi-init --serverdir "$SERVERDIR" --root-passwd "$DEVPI_ROOT_PASSWORD" $NO_ROOT_PYPI_ARG
fi

# Постоянный секрет: без него каждый рестарт генерит новый случайный секрет и
# инвалидирует токены логина — из-за этого падал devpi index -c.
SECRET_FILE="$SERVERDIR/.devpi-secret"
if [ ! -s "$SECRET_FILE" ]; then
    echo "Создаю постоянный секрет: $SECRET_FILE"
    devpi-gen-secret --secretfile "$SECRET_FILE"
fi

# Поднимаем сервер в фоне, держим его PID — на нём контейнер и будет жить.
devpi-server --host "$HOST" --port "$PORT" --serverdir "$SERVERDIR" --secretfile "$SECRET_FILE" $RESTRICT_MODIFY_ARG $AUTOCREATE_USERS_ARG &
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

# Локальный пользователь для автоматической загрузки и наполнения root/pypi
# пакетами, заранее скачанными из PyPI.
if [ -n "$LOCAL_UPLOAD_USER" ]; then
    devpi user -c "$LOCAL_UPLOAD_USER" "password=$LOCAL_UPLOAD_PASSWORD" || devpi user -m "$LOCAL_UPLOAD_USER" "password=$LOCAL_UPLOAD_PASSWORD"
fi

# root/pypi — локальный stage, а не внешний mirror. Он наполняется только
# физически загруженными пакетами. release наследуется от него, test — от release.
configure_index "root/$PYPI_INDEX" "$PYPI_INDEX_BASES" "$PYPI_VOLATILE" "$PYPI_ACL_UPLOAD"
configure_index "root/$RELEASE_INDEX" "$RELEASE_INDEX_BASES" "$RELEASE_VOLATILE" "$ACL_UPLOAD"
configure_index "root/$TEST_INDEX" "$TEST_INDEX_BASES" "$TEST_VOLATILE" "$ACL_UPLOAD"

# Логинимся локальным upload-пользователем, чтобы new_release.py заливал
# стабильные и тестовые версии без root-кредов.
if [ -n "$LOCAL_UPLOAD_USER" ]; then
    devpi login "$LOCAL_UPLOAD_USER" --password "$LOCAL_UPLOAD_PASSWORD"
else
    devpi login root --password "$DEVPI_ROOT_PASSWORD"
fi

# Запускаем мониторинг релизов. Падение скрипта не должно ронять контейнер.
python3 /git/new_release.py || echo "new_release.py завершился с кодом: $?"

# Держим контейнер на переднем плане самим сервером.
wait "$SERVER_PID"
