#!/usr/bin/env bash
# Собрать Docker-образы всех 4 сервисов DBOS и импортировать их в k3s.
# Запускать на VM, где установлен и docker, и k3s.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

TAG="${1:-latest}"

declare -A SERVICES=(
    [auth-service]="auth_service"
    [logging-service]="loging_service"
    [server-service]="server_service"
    [server-worker]="server_worker"
)

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

for image in "${!SERVICES[@]}"; do
    dir="${SERVICES[$image]}"
    echo "→ Собираем dbos/${image}:${TAG}..."
    docker build \
        -t "dbos/${image}:${TAG}" \
        -f "$ROOT_DIR/${dir}/docker/Dockerfile" \
        "$ROOT_DIR/${dir}"
done

for image in "${!SERVICES[@]}"; do
    echo "→ Экспортируем dbos/${image}:${TAG}..."
    docker save "dbos/${image}:${TAG}" -o "$TMP/${image}.tar"
done

for image in "${!SERVICES[@]}"; do
    echo "→ Импортируем dbos/${image}:${TAG} в k3s containerd..."
    sudo k3s ctr images import "$TMP/${image}.tar"
done

echo ""
echo "✓ Образы готовы и доступны k3s:"
sudo k3s ctr images list | grep -E "dbos/(auth|logging|server)-(service|worker)" || true

echo ""
echo "  Чтобы развернуть/обновить: scripts/k8s/deploy.sh"
