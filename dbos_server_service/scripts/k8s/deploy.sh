#!/usr/bin/env bash
# Применить все k8s-манифесты и подождать готовности.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/../../k8s" && pwd)"

if [[ ! -f "$K8S_DIR/20-secrets.yaml" ]] || [[ ! -f "$K8S_DIR/50-ingress.yaml" ]]; then
    echo "ОШИБКА: $K8S_DIR/20-secrets.yaml или 50-ingress.yaml не существует."
    echo "  Запустите сначала: scripts/k8s/gen_secrets.sh <domain>"
    exit 1
fi

echo "→ Применяем манифесты..."
kubectl apply -k "$K8S_DIR"

echo ""
echo "→ Ждём готовности pods (timeout 5 мин)..."
kubectl -n dbos rollout status deploy/auth-postgres    --timeout=300s
kubectl -n dbos rollout status deploy/logging-postgres --timeout=300s
kubectl -n dbos rollout status deploy/logging-service  --timeout=300s
kubectl -n dbos rollout status deploy/auth-service     --timeout=300s

echo ""
echo "✓ Все deployments готовы."
echo ""
kubectl -n dbos get pods,svc

echo ""
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
DOMAIN=$(grep -E "^INGRESS_HOST" "$K8S_DIR/.env.k8s" 2>/dev/null | cut -d= -f2)
echo "Доступ снаружи (через Traefik Ingress):"
echo "  auth_service    →  https://${DOMAIN}/api/auth/v1/docs"
echo "  logging_service →  https://${DOMAIN}/api/logging/v1/docs"
echo ""
echo "  Пропиши на клиенте, если домен не в DNS:"
echo "    echo '${NODE_IP} ${DOMAIN}' | sudo tee -a /etc/hosts"
echo ""
echo "  Self-signed cert — браузер покажет предупреждение, нужно один раз принять."
