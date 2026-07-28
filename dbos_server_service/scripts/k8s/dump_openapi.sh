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
    [secret-service]=secret.json
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

# По одному ConfigMap'у на spec: server.json уже ~0.8 МБ, а суммарный
# ConfigMap упирается в лимит 1 МБ. Старый общий dbos-openapi-specs удаляем.
echo "→ Пересоздаём ConfigMap'ы спецификаций (по одному на сервис)..."
kubectl -n dbos delete configmap dbos-openapi-specs --ignore-not-found
declare -A SPEC_TO_CM=(
    [auth.json]=dbos-openapi-auth
    [logging.json]=dbos-openapi-logging
    [server.json]=dbos-openapi-server
    [secret.json]=dbos-openapi-secret
)
for spec in "${!SPEC_TO_CM[@]}"; do
    cm="${SPEC_TO_CM[$spec]}"
    kubectl -n dbos delete configmap "$cm" --ignore-not-found
    kubectl -n dbos create configmap "$cm" --from-file="$spec=$TMP/$spec"
done

echo "→ Рестартим dbos-swagger-ui (чтобы подхватил новые файлы)..."
kubectl -n dbos rollout restart deploy/dbos-swagger-ui
kubectl -n dbos rollout status deploy/dbos-swagger-ui --timeout=120s

echo ""
echo "✓ Готово. UI: https://<host>/docs/"
