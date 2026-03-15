#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_HOST="${REGISTRY_HOST:-allta.devos.astralinux.ru:21503}"
LOCAL_IMAGE_NAME="${LOCAL_IMAGE_NAME:-allta-vm-cli}"
REMOTE_IMAGE_REPO="${REMOTE_IMAGE_REPO:-${REGISTRY_HOST}/allta-vm-cli}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
BASE_IMAGE_NAME="allta-vm-cli-base:debian10-py312"
IMAGE_NAME="allta-vm-cli-builder:debian10"
PUBLISHED_BASE_IMAGE="${PUBLISHED_BASE_IMAGE:-${REGISTRY_HOST}/cli:${IMAGE_TAG}}"
UPLOAD_HOST="${UPLOAD_HOST:-10.177.103.10}"

sudo_cmd() {
    if [ "${EUID}" -eq 0 ]; then
        return 1
    fi

    if command -v sudo >/dev/null 2>&1; then
        return 0
    fi

    echo "sudo не найден, а запуск не от root" >&2
    exit 1
}

run_root() {
    if sudo_cmd; then
        sudo "$@"
    else
        "$@"
    fi
}

run_docker() {
    if docker info >/dev/null 2>&1; then
        docker "$@"
        return
    fi

    if sudo_cmd; then
        sudo docker "$@"
        return
    fi

    docker "$@"
}

latest_deb() {
    local deb_file

    deb_file="$(ls -1t "${SCRIPT_DIR}"/allta-vm*.deb 2>/dev/null | head -n 1 || true)"
    if [ -z "${deb_file}" ]; then
        echo "Не найден .deb файл в ${SCRIPT_DIR}" >&2
        exit 1
    fi

    printf '%s\n' "${deb_file}"
}

precond() {
    run_root apt-get update
    run_root apt-get install -y docker.io

    if command -v systemctl >/dev/null 2>&1; then
        run_root systemctl enable --now docker || true
    fi

    if command -v service >/dev/null 2>&1; then
        run_root service docker start || true
    fi

    echo "Docker установлен"
}

start() {
    echo "Подтягиваю базовый образ ${PUBLISHED_BASE_IMAGE}..."
    run_docker pull "${PUBLISHED_BASE_IMAGE}"

    run_docker build \
        --build-arg "BASE_IMAGE=${PUBLISHED_BASE_IMAGE}" \
        -t "${IMAGE_NAME}" \
        -f "${SCRIPT_DIR}/srv/Dockerfile" \
        "${SCRIPT_DIR}/srv"

    run_docker run --rm \
        -v "${SCRIPT_DIR}:/out" \
        "${IMAGE_NAME}"

    run_root chmod 0644 "${SCRIPT_DIR}"/allta-vm*.deb
}

build_base() {
    run_docker build \
        -t "${BASE_IMAGE_NAME}" \
        -f "${SCRIPT_DIR}/Dockerfile.base" \
        "${SCRIPT_DIR}"
}

docker_img() {
    local local_tag remote_tag

    local_tag="${LOCAL_IMAGE_NAME}:${IMAGE_TAG}"
    remote_tag="${REMOTE_IMAGE_REPO}:${IMAGE_TAG}"

    echo "Собираю базовый образ ${BASE_IMAGE_NAME}..."
    build_base

    echo "Тегирую образ: ${local_tag}"
    run_docker tag "${BASE_IMAGE_NAME}" "${local_tag}"

    echo "Тегирую образ для registry: ${remote_tag}"
    run_docker tag "${BASE_IMAGE_NAME}" "${remote_tag}"

    echo "Публикую образ в registry: ${remote_tag}"
    run_docker push "${remote_tag}"
}

upload() {
    local username deb_file deb_name remote_target

    username="${1:-}"
    if [ -z "${username}" ]; then
        echo "Нужно указать имя пользователя." >&2
        echo "Использование: ./install.sh upload <username>" >&2
        exit 1
    fi

    deb_file="$(latest_deb)"
    deb_name="$(basename "${deb_file}")"
    remote_target="${username}@${UPLOAD_HOST}:~"

    echo "Загружаю пакет на ${remote_target}: ${deb_name}"
    scp "${deb_file}" "${remote_target}"

    echo "Обновляю пакет на ${username}@${UPLOAD_HOST}:/srv/ftp"
    ssh "${username}@${UPLOAD_HOST}" \
        "sudo rm -f /srv/ftp/boxes/allta-vm*.deb && sudo mv \"\$HOME/${deb_name}\" /srv/ftp/boxes/ && sudo chmod 640 /srv/ftp/boxes/allta-vm*.deb"
}

usage() {
    echo "Использование:"
    echo "  ./install.sh precond"
    echo "  ./install.sh build-base"
    echo "  ./install.sh docker_img"
    echo "  ./install.sh upload <username>"
    echo "  ./install.sh start"
}

case "${1:-}" in
    precond)
        precond
        ;;
    build-base)
        build_base
        ;;
    docker_img)
        docker_img
        ;;
    upload)
        upload "${2:-}"
        ;;
    start)
        start
        ;;
    *)
        usage
        exit 1
        ;;
esac
