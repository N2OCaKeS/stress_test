#!/usr/bin/env bash
# Обновить образы и перезапустить deployments (rolling update).
# Использование:
#   scripts/k8s/rollout.sh                — все 4 сервиса
#   scripts/k8s/rollout.sh auth           — только auth_service
#   scripts/k8s/rollout.sh logging        — только logging_service
#   scripts/k8s/rollout.sh server         — только server_service
#   scripts/k8s/rollout.sh worker         — только server_worker

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-all}"

build_one() {
    local svc=$1
    local dir=$2
    echo "→ Пересобираем dbos/${svc}:latest..."
    docker build -t "dbos/${svc}:latest" -f "$dir/docker/Dockerfile" "$dir"
    local tmp=$(mktemp)
    docker save "dbos/${svc}:latest" -o "$tmp"
    sudo k3s ctr images import "$tmp"
    rm -f "$tmp"
}

rollout_one() {
    local deploy=$1
    echo "→ Rolling update deploy/${deploy}..."
    kubectl -n dbos rollout restart deploy/"${deploy}"
    kubectl -n dbos rollout status  deploy/"${deploy}" --timeout=300s
}

ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

case "$TARGET" in
    auth|all)
        build_one "auth-service" "$ROOT_DIR/auth_service"
        rollout_one "auth-service"
        ;;
esac

case "$TARGET" in
    logging|all)
        build_one "logging-service" "$ROOT_DIR/loging_service"
        rollout_one "logging-service"
        ;;
esac

case "$TARGET" in
    server|all)
        build_one "server-service" "$ROOT_DIR/server_service"
        rollout_one "server-service"
        ;;
esac

case "$TARGET" in
    worker|all)
        build_one "server-worker" "$ROOT_DIR/server_worker"
        rollout_one "server-worker"
        ;;
esac

echo ""
echo "✓ Rollout завершён."
kubectl -n dbos get pods
