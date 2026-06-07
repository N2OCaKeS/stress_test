#!/usr/bin/env bash
# Извлекает openapi-spec'ы из работающих pod'ов трёх сервисов и
# пересоздаёт ConfigMap `dbos-openapi-specs` (его читает dbos-swagger-ui).
#
# Зачем: в prod `/openapi.json` отключён (намеренно), а спецификации
# нужны общему Swagger UI на /docs. После любого изменения endpoint'ов
# и rolling-update — перезапусти этот скрипт + restart swagger-ui.
#
# Использование:
#   sudo scripts/k8s/dump_openapi.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "ОШИБКА: нужны права root (для kubectl с k3s.yaml). Запустите через sudo." >&2
    exit 1
fi

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

declare -A SVC_TO_FILE=(
    [auth-service]=auth.json
    [logging-service]=logging.json
    [server-service]=server.json
)

for svc in "${!SVC_TO_FILE[@]}"; do
    file="${SVC_TO_FILE[$svc]}"
    pod=$(kubectl -n dbos get pods -l app="$svc" --no-headers | grep "1/1" | head -1 | awk '{print $1}')
    if [[ -z "$pod" ]]; then
        echo "ОШИБКА: не нашёл готовый pod для $svc" >&2
        exit 1
    fi
    echo "→ $svc / $pod → $file"
    kubectl -n dbos exec "$pod" -- python -c \
        'import json, sys; from src.main import app; sys.stdout.write(json.dumps(app.openapi()))' \
        2>/dev/null | grep -o '{.*' > "$TMP/$file"
    [[ -s "$TMP/$file" ]] || { echo "ОШИБКА: пустой spec для $svc" >&2; exit 1; }
done

echo "→ Пересоздаём ConfigMap dbos-openapi-specs..."
kubectl -n dbos delete configmap dbos-openapi-specs --ignore-not-found
kubectl -n dbos create configmap dbos-openapi-specs \
    --from-file=auth.json="$TMP/auth.json" \
    --from-file=logging.json="$TMP/logging.json" \
    --from-file=server.json="$TMP/server.json"

echo "→ Рестартим dbos-swagger-ui (чтобы подхватил новые файлы)..."
kubectl -n dbos rollout restart deploy/dbos-swagger-ui
kubectl -n dbos rollout status deploy/dbos-swagger-ui --timeout=120s

echo ""
echo "✓ Готово. UI: https://<host>/docs/"
