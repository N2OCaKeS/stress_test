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

# ── Durable keystore bootstrap (до сервисов, create-only) ─────────────────────
# server/secret-service стартуют с KEYSTORE_BACKEND=k8s и читают master-ключи из
# Secret'ов dbos-server-encryption-keys / dbos-secret-encryption-keys. Их надо
# создать ДО того, как поднимутся pod'ы, иначе keystore пуст → crashloop.
#
# create-only (не apply!): живой keystore с уже ротированными ключами никогда не
# перетирается. AlreadyExists — норма (повторный деплой). Namespace создаём
# первым, т.к. он нужен и для keystore, и для всего остального.
KEYSTORE_OUT="$K8S_DIR/21-keystore-secrets.yaml"
KEYSTORE_NAMES=(dbos-server-encryption-keys dbos-secret-encryption-keys)

echo "→ Создаём namespace dbos (если нет)..."
kubectl apply -f "$K8S_DIR/00-namespace.yaml"

if [[ -f "$KEYSTORE_OUT" ]]; then
    echo "→ Bootstrap durable keystore (create-only, живой keystore не трогаем)..."
    set +e
    create_out=$(kubectl create -f "$KEYSTORE_OUT" 2>&1)
    set -e
    # Показываем всё, кроме безобидного AlreadyExists (keystore уже засеян).
    echo "$create_out" | grep -v "AlreadyExists" || true
fi

# Гарантируем, что keystore-Secret'ы реально на месте — иначе сервисы упадут.
for name in "${KEYSTORE_NAMES[@]}"; do
    if ! kubectl -n dbos get secret "$name" >/dev/null 2>&1; then
        echo "ОШИБКА: keystore-Secret $name отсутствует и не сгенерирован."
        echo "  Запустите 'make k8s-secrets' (создаст $KEYSTORE_OUT) и повторите."
        exit 1
    fi
done
echo "✓ Keystore-Secret'ы на месте: ${KEYSTORE_NAMES[*]}"

echo ""
echo "→ Применяем манифесты..."
# `kubectl apply -k` НЕ принимает `--load-restrictor`; флаг живёт только на
# `kubectl kustomize` (build-step). configMapGenerator `rotation-scripts`
# тянет `../scripts/k8s/*.sh`, что вне k8s/, и без флага kustomize отбивает
# security'ом. Поэтому строим manifests через `kubectl kustomize` с разрешением
# load-restrictor, а apply'им через pipe.
kubectl kustomize "$K8S_DIR" --load-restrictor=LoadRestrictionsNone | kubectl apply -f -

# Базы данных поднимаем ДО миграций и ДО сервисов: migration-Job'у нужна живая
# БД, а схема-зависимые сервисы (auth-service делает SELECT count(*) FROM users
# в bootstrap'е и крашится, если таблицы нет) не должны ждать готовности раньше,
# чем миграции создадут схему.
echo ""
echo "→ Ждём готовности баз данных (timeout 5 мин)..."
for pg in auth-postgres logging-postgres server-postgres worker-postgres secret-postgres; do
    kubectl -n dbos rollout status "deploy/$pg" --timeout=300s
done

# ── Миграции отдельными Job'ами ДО ожидания сервисов ──────────────────────────
# Job'ы из 45-migrations.yaml применяем явным `kubectl apply -f` (в kustomization
# их нет — Job immutable, повторный `apply -k` бы падал). Идемпотентность —
# delete по именам перед apply: каждый деплой гоняет свежий Job. Только после
# того как все миграции дошли до condition=complete, ждём rollout сервисов —
# к этому моменту схема на месте и auth-service переживает рестарт без crashloop.
MIGRATE_JOBS=(auth-migrate logging-migrate server-migrate worker-migrate secret-migrate)

echo ""
echo "→ Сбрасываем прежние migration-Job'ы (Job'ы immutable, нужен чистый запуск)..."
kubectl -n dbos delete job "${MIGRATE_JOBS[@]}" --ignore-not-found

echo "→ Применяем migration-Job'ы (45-migrations.yaml)..."
kubectl apply -f "$K8S_DIR/45-migrations.yaml"

echo "→ Ждём завершения миграций (timeout 5 мин на каждую)..."
for job in "${MIGRATE_JOBS[@]}"; do
    if ! kubectl -n dbos wait --for=condition=complete "job/$job" --timeout=300s; then
        echo "ОШИБКА: миграция $job не завершилась за отведённое время."
        echo "  Последние строки лога:"
        kubectl -n dbos logs "job/$job" --tail=50 || true
        exit 1
    fi
    echo "✓ $job завершён."
done

echo ""
echo "→ Ждём готовности сервисов (timeout 5 мин)..."
for svc in redis logging-service auth-service server-service server-worker secret-service; do
    kubectl -n dbos rollout status "deploy/$svc" --timeout=300s
done

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
