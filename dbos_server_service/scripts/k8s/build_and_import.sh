#!/usr/bin/env bash
# Собрать Docker-образы auth_service и logging_service и импортировать их в k3s.
# Запускать на VM, где установлен и docker, и k3s.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Тэг по умолчанию — latest. Можно передать первым аргументом: ./build_and_import.sh v1.2.3
TAG="${1:-latest}"

echo "→ Собираем dbos/auth-service:${TAG}..."
docker build \
    -t "dbos/auth-service:${TAG}" \
    -f "$ROOT_DIR/auth_service/docker/Dockerfile" \
    "$ROOT_DIR/auth_service"

echo "→ Собираем dbos/logging-service:${TAG}..."
docker build \
    -t "dbos/logging-service:${TAG}" \
    -f "$ROOT_DIR/loging_service/docker/Dockerfile" \
    "$ROOT_DIR/loging_service"

echo "→ Экспортируем образы в tar..."
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

docker save "dbos/auth-service:${TAG}" -o "$TMP/auth.tar"
docker save "dbos/logging-service:${TAG}" -o "$TMP/logging.tar"

echo "→ Импортируем в k3s containerd..."
sudo k3s ctr images import "$TMP/auth.tar"
sudo k3s ctr images import "$TMP/logging.tar"

echo ""
echo "✓ Образы готовы и доступны k3s:"
sudo k3s ctr images list | grep -E "dbos/(auth|logging)-service" || true

echo ""
echo "  Чтобы развернуть/обновить: scripts/k8s/deploy.sh"
