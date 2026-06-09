#!/usr/bin/env bash
# Установка cert-manager в closed network.
#
# Предусловия:
#   - k3s установлен и kubectl доступен (KUBECONFIG=/etc/rancher/k3s/k3s.yaml)
#   - scripts/k8s/cert-manager.yaml уже лежит на VM (см. шапку
#     k8s/90-cert-manager-install.yaml — там команды для скачивания)
#   - docker-образы cert-manager импортированы в containerd k3s
#
# Идемпотентный: можно перезапускать.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/../../k8s" && pwd)"
CM_MANIFEST="$SCRIPT_DIR/cert-manager.yaml"
CM_VERSION="${CM_VERSION:-v1.15.3}"

if [[ $EUID -ne 0 ]]; then
    echo "ОШИБКА: нужны права root (для kubectl с k3s.yaml). Запустите через sudo." >&2
    exit 1
fi

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

if [[ ! -f "$CM_MANIFEST" ]]; then
    echo "ОШИБКА: $CM_MANIFEST не найден." >&2
    echo "  Скачайте на машине с интернетом:" >&2
    echo "    curl -sL -o $CM_MANIFEST \\" >&2
    echo "      https://github.com/cert-manager/cert-manager/releases/download/${CM_VERSION}/cert-manager.yaml" >&2
    echo "  И перенесите файл на эту VM в \$SCRIPT_DIR." >&2
    exit 1
fi

# ── 1. Apply манифеста cert-manager ───────────────────────────────────────────
echo "→ Применяем cert-manager ($CM_VERSION) из $CM_MANIFEST..."
kubectl apply -f "$CM_MANIFEST"

# ── 2. Дождаться готовности всех трёх Deployment'ов ───────────────────────────
echo "→ Ждём rollout cert-manager-controller / cainjector / webhook (timeout 300s)..."
for d in cert-manager cert-manager-cainjector cert-manager-webhook; do
    kubectl -n cert-manager rollout status "deploy/$d" --timeout=300s
done

# ── 3. Sanity check webhook — он должен отвечать на validation, иначе
#       последующие apply Certificate/Issuer провалятся с
#       "x509: certificate signed by unknown authority". ────────────────────────
echo "→ Проверяем доступность webhook через dry-run..."
TMP=$(mktemp)
trap "rm -f $TMP" EXIT
cat > "$TMP" <<'EOF'
apiVersion: cert-manager.io/v1
kind: Issuer
metadata:
  name: webhook-probe
  namespace: cert-manager
spec:
  selfSigned: {}
EOF

# Ретраи на случай, если webhook готов, но ещё не зарегистрирован в API.
for i in $(seq 1 30); do
    if kubectl apply --dry-run=server -f "$TMP" >/dev/null 2>&1; then
        echo "  ✓ webhook отвечает (попытка $i)"
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "ОШИБКА: webhook не отвечает после 30 попыток." >&2
        kubectl -n cert-manager get pods >&2
        exit 1
    fi
    sleep 2
done

# ── 4. Применяем dbos-namespace, чтобы Issuer и Certificate в нём могли
#       быть применены этим же скриптом (если намерены сразу всё ставить). ─────
echo "→ Применяем k8s/00-namespace.yaml (если ещё не применён)..."
kubectl apply -f "$K8S_DIR/00-namespace.yaml"

# ── 5. CA Issuer + leaf Certificates ───────────────────────────────────────────
echo "→ Применяем 91-ca-issuer.yaml..."
kubectl apply -f "$K8S_DIR/91-ca-issuer.yaml"

echo "→ Ждём, пока bootstrap Issuer подпишет корневой CA-сертификат (timeout 120s)..."
kubectl -n dbos wait --for=condition=Ready certificate/dbos-internal-ca --timeout=120s

# Leaf-сертификаты per-service (auth/logging/server/secret) больше не выпускаются —
# cluster-internal вызовы идут plain http через NetworkPolicy default-deny, edge
# TLS обеспечивает Ingress через готовый `dbos-ingress-tls` от gen_secrets.sh.
# Если 92-certificates.yaml вернётся (mTLS-режим) — apply раскомментировать.
# kubectl apply -f "$K8S_DIR/92-certificates.yaml"

echo ""
echo "✓ cert-manager установлен, CA выпущен."
echo ""
echo "  Корневой CA: Secret dbos/dbos-ca-key-pair (ca.crt + tls.crt + tls.key)"
echo "  CA-резерв:"
kubectl -n dbos get certificate dbos-internal-ca
echo ""
echo "  Извлечь CA-сертификат для импорта на admin-машины:"
echo "    kubectl -n dbos get secret dbos-ca-key-pair -o jsonpath='{.data.ca\\.crt}' | base64 -d > dbos-ca.crt"
echo ""
echo "  Дальше: make k8s-deploy (применит Ingress, который подхватит TLS-secret'ы)."
