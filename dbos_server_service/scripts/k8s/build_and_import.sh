#!/usr/bin/env bash
# Собрать Docker-образы 5 сервисов DBOS + web-ui (SPA) и импортировать их в k3s.
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
    [secret-service]="secret_service"
)

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
fi

for image in "${!SERVICES[@]}"; do
    dir="${SERVICES[$image]}"
    echo "→ Собираем dbos/${image}:${TAG}..."
    $DOCKER build \
        -t "dbos/${image}:${TAG}" \
        -f "$ROOT_DIR/${dir}/docker/Dockerfile" \
        "$ROOT_DIR/${dir}"
done

for image in "${!SERVICES[@]}"; do
    echo "→ Экспортируем dbos/${image}:${TAG}..."
    $DOCKER save "dbos/${image}:${TAG}" -o "$TMP/${image}.tar"
done
sudo chmod 644 "$TMP"/*.tar 2>/dev/null || true

K3S_BIN="$(command -v k3s || echo /usr/local/bin/k3s)"

for image in "${!SERVICES[@]}"; do
    echo "→ Импортируем dbos/${image}:${TAG} в k3s containerd..."
    sudo "$K3S_BIN" ctr images import "$TMP/${image}.tar"
done

# web-ui: отдельный multi-stage Dockerfile (vite build → nginx), путь и
# контекст отличаются от сервисов, поэтому собираем отдельным шагом.
echo "→ Собираем dbos/web-ui:${TAG}..."
$DOCKER build -t "dbos/web-ui:${TAG}" -f "$ROOT_DIR/web_ui/Dockerfile" "$ROOT_DIR/web_ui"
$DOCKER save "dbos/web-ui:${TAG}" -o "$TMP/web-ui.tar"
sudo chmod 644 "$TMP/web-ui.tar" 2>/dev/null || true
echo "→ Импортируем dbos/web-ui:${TAG} в k3s containerd..."
sudo "$K3S_BIN" ctr images import "$TMP/web-ui.tar"

echo ""
echo "✓ Образы готовы и доступны k3s:"
sudo "$K3S_BIN" ctr images list | grep -E "dbos/(auth|logging|server|secret)-(service|worker)|dbos/web-ui" || true

echo ""
echo "  Чтобы развернуть/обновить: scripts/k8s/deploy.sh"
