#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_HOST="${REGISTRY_HOST:-allta.devos.astralinux.ru:21503}"
LOCAL_IMAGE_NAME="${LOCAL_IMAGE_NAME:-cli}"
REMOTE_IMAGE_REPO="${REMOTE_IMAGE_REPO:-${REGISTRY_HOST}/cli}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
BASE_IMAGE_NAME="allta-cli-base:debian10-py312"
IMAGE_NAME="allta-cli-builder:debian10"
PUBLISHED_BASE_IMAGE="${PUBLISHED_BASE_IMAGE:-${REMOTE_IMAGE_REPO}:${IMAGE_TAG}}"
UPLOAD_HOST="${UPLOAD_HOST:-10.177.103.10}"
ALLTA_API_HOST="${ALLTA_API_HOST:-allta.devos.astralinux.ru}"
ALLTA_API_PORT="${ALLTA_API_PORT:-21500}"
ALLTA_SHARED_CERT_PATH="${ALLTA_SHARED_CERT_PATH:-/var/allta_services/certs/allta-api.crt}"
ALLTA_SYSTEM_CA_TARGET="${ALLTA_SYSTEM_CA_TARGET:-/usr/local/share/ca-certificates/allta-api.crt}"
DEB_STAGING_DIR="${DEB_STAGING_DIR:-/tmp/allta-cli-debs}"

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

fetch_api_cert_from_endpoint() {
    local target="$1"

    if ! command -v openssl >/dev/null 2>&1; then
        return 1
    fi

    if command -v timeout >/dev/null 2>&1; then
        timeout 8 openssl s_client \
            -servername "${ALLTA_API_HOST}" \
            -connect "${ALLTA_API_HOST}:${ALLTA_API_PORT}" \
            < /dev/null 2>/dev/null \
            | sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p' > "${target}" || true
    else
        openssl s_client \
            -servername "${ALLTA_API_HOST}" \
            -connect "${ALLTA_API_HOST}:${ALLTA_API_PORT}" \
            < /dev/null 2>/dev/null \
            | sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p' > "${target}" || true
    fi

    [ -s "${target}" ] && openssl x509 -in "${target}" -noout >/dev/null 2>&1
}

resolve_api_cert_source() {
    local tmp_cert

    tmp_cert="$(mktemp)"
    if fetch_api_cert_from_endpoint "${tmp_cert}"; then
        run_root mkdir -p "$(dirname "${ALLTA_SHARED_CERT_PATH}")"
        if [ ! -f "${ALLTA_SHARED_CERT_PATH}" ] || ! cmp -s "${tmp_cert}" "${ALLTA_SHARED_CERT_PATH}"; then
            run_root install -m 0644 "${tmp_cert}" "${ALLTA_SHARED_CERT_PATH}"
        fi
        rm -f "${tmp_cert}"
        printf '%s\n' "${ALLTA_SHARED_CERT_PATH}"
        return 0
    fi

    rm -f "${tmp_cert}"
    if [ -s "${ALLTA_SHARED_CERT_PATH}" ] && openssl x509 -in "${ALLTA_SHARED_CERT_PATH}" -noout >/dev/null 2>&1; then
        printf '%s\n' "${ALLTA_SHARED_CERT_PATH}"
        return 0
    fi

    return 1
}

install_system_ca() {
    local cert_source="$1"

    if ! run_root sh -c 'command -v update-ca-certificates >/dev/null 2>&1'; then
        echo "update-ca-certificates не найден, пропускаю системный trust store." >&2
        return 0
    fi

    if [ -f "${ALLTA_SYSTEM_CA_TARGET}" ] && cmp -s "${cert_source}" "${ALLTA_SYSTEM_CA_TARGET}"; then
        return 0
    fi

    run_root install -m 0644 "${cert_source}" "${ALLTA_SYSTEM_CA_TARGET}"
    run_root update-ca-certificates >/dev/null
}

configure_docker_registry_ca() {
    local cert_source="$1"
    local docker_ca_dir="/etc/docker/certs.d/${REGISTRY_HOST}"
    local docker_ca_file="${docker_ca_dir}/ca.crt"

    run_root mkdir -p "${docker_ca_dir}"
    if [ -f "${docker_ca_file}" ] && cmp -s "${cert_source}" "${docker_ca_file}"; then
        return 0
    fi
    run_root install -m 0644 "${cert_source}" "${docker_ca_file}"
}

ensure_api_tls_trust() {
    local cert_source

    cert_source="$(resolve_api_cert_source)" || {
        echo "Не удалось получить CA сертификат от API (${ALLTA_API_HOST}:${ALLTA_API_PORT})." >&2
        exit 1
    }

    install_system_ca "${cert_source}"
    configure_docker_registry_ca "${cert_source}"
}

latest_deb() {
    local deb_file

    deb_file="$(ls -1t "${SCRIPT_DIR}"/allta*.deb 2>/dev/null | head -n 1 || true)"
    if [ -z "${deb_file}" ]; then
        echo "Не найден .deb файл в ${SCRIPT_DIR}" >&2
        exit 1
    fi

    printf '%s\n' "${deb_file}"
}

grant_apt_read_access() {
    local target_file="$1"
    local apt_user="_apt"
    local current

    if ! id -u "${apt_user}" >/dev/null 2>&1; then
        return 0
    fi

    if ! command -v setfacl >/dev/null 2>&1; then
        echo "setfacl не найден: не могу выдать точечный доступ пользователю _apt к ${target_file}" >&2
        echo "Установите пакет acl или используйте ./install.sh install" >&2
        return 0
    fi

    run_root setfacl -m "u:${apt_user}:r" "${target_file}" || true

    current="$(dirname "${target_file}")"
    while :; do
        run_root setfacl -m "u:${apt_user}:rx" "${current}" || true
        [ "${current}" = "/" ] && break
        current="$(dirname "${current}")"
    done
}

prepare_deb_permissions() {
    local deb_file="$1"

    run_root chmod 0644 "${deb_file}"
    grant_apt_read_access "${deb_file}"
}

stage_latest_deb_for_install() {
    local deb_file deb_name staged_path

    deb_file="$(latest_deb)"
    prepare_deb_permissions "${deb_file}"
    deb_name="$(basename "${deb_file}")"
    staged_path="${DEB_STAGING_DIR}/${deb_name}"

    run_root install -d -m 0755 "${DEB_STAGING_DIR}"
    run_root install -m 0644 "${deb_file}" "${staged_path}"
    printf '%s\n' "${staged_path}"
}

precond() {
    run_root apt-get update
    run_root apt-get install -y docker.io openssl ca-certificates acl

    if command -v systemctl >/dev/null 2>&1; then
        run_root systemctl enable --now docker || true
    fi

    if command -v service >/dev/null 2>&1; then
        run_root service docker start || true
    fi

    ensure_api_tls_trust

    echo "Docker установлен"
}

start() {
    local deb_file staged_deb

    ensure_api_tls_trust

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

    deb_file="$(latest_deb)"
    prepare_deb_permissions "${deb_file}"
    staged_deb="$(stage_latest_deb_for_install)"
    echo "Пакет для ручной установки: ${deb_file}"
    echo "Пакет подготовлен для установки: ${staged_deb}"
}

build_base() {
    run_docker build \
        -t "${BASE_IMAGE_NAME}" \
        -f "${SCRIPT_DIR}/Dockerfile.base" \
        "${SCRIPT_DIR}"
}

docker_img() {
    local local_tag remote_tag

    ensure_api_tls_trust

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
    prepare_deb_permissions "${deb_file}"
    deb_name="$(basename "${deb_file}")"
    remote_target="${username}@${UPLOAD_HOST}:~"

    echo "Загружаю пакет на ${remote_target}: ${deb_name}"
    scp "${deb_file}" "${remote_target}"

    echo "Обновляю пакет на ${username}@${UPLOAD_HOST}:/srv/ftp"
    ssh "${username}@${UPLOAD_HOST}" \
        "sudo rm -f /srv/ftp/allta*.deb && sudo install -m 0644 \"\$HOME/${deb_name}\" /srv/ftp/${deb_name} && sudo rm -f \"\$HOME/${deb_name}\""
}

install_deb() {
    local staged_deb
    staged_deb="$(stage_latest_deb_for_install)"
    run_root apt-get install -y "${staged_deb}"
}

usage() {
    echo "Использование:"
    echo "  ./install.sh precond"
    echo "  ./install.sh trust"
    echo "  ./install.sh build-base"
    echo "  ./install.sh docker_img"
    echo "  ./install.sh install"
    echo "  ./install.sh upload <username>"
    echo "  ./install.sh start"
}

case "${1:-}" in
    precond)
        precond
        ;;
    trust)
        ensure_api_tls_trust
        ;;
    build-base)
        build_base
        ;;
    docker_img)
        docker_img
        ;;
    install)
        install_deb
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
