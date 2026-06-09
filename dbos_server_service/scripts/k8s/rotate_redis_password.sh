#!/usr/bin/env bash
# Ротация Redis-пароля.
#
# Почему НЕ через `CONFIG SET requirepass`:
#   readinessProbe для redis-pod'а в k8s/12-redis.yaml жёстко прибит к
#   env-переменной REDIS_PASSWORD:
#       readinessProbe:
#         exec:
#           command: [redis-cli, -a, $(REDIS_PASSWORD), ping]
#   Если сделать CONFIG SET requirepass <new>, requirepass станет <new>,
#   но env в pod'е (и в команде probe) останется <old>. Probe начнёт
#   получать WRONGPASS → pod → NotReady → Service drop endpoints → все
#   consumer'ы (auth/server/secret/worker) теряют доступ к Redis. Это RED.
#
# Новый flow (без CONFIG SET):
#   0. Pre-check: текущий REDIS_PASSWORD из Secret'а реально подходит к pod'у.
#   1. Сгенерировать новый пароль (32 символа [A-Za-z0-9]).
#   2. Сохранить старый пароль в /tmp/dbos-rotate-redis-<ts>.txt (chmod 600),
#      туда же новый — для recovery при сбое в середине flow.
#   3. Patch dbos-secrets.REDIS_PASSWORD <new>.
#   4. Rolling restart deploy/redis (strategy: Recreate в манифесте, ~5-15s
#      downtime — pod пересоздаётся с новым env, --requirepass подставляется
#      из нового Secret'а).
#   5. Rolling restart consumer'ов: auth-service, server-service,
#      server-worker, secret-service. logging-service в списке НЕТ — он
#      Redis не использует (проверено grep REDIS_PASSWORD k8s/*.yaml).
#   6. kubectl rollout status для redis + 4 consumer'ов.
#   7. Verify: redis-cli -a <new> ping внутри pod'а → PONG.
#
# Использование:
#   scripts/k8s/rotate_redis_password.sh           # полный flow (интерактивно)
#   scripts/k8s/rotate_redis_password.sh --yes     # полный flow без prompt'ов (для CronJob)
#   scripts/k8s/rotate_redis_password.sh --status  # текущее состояние
#
# Требования: kubectl, jq, openssl.

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
REDIS_DEPLOY="redis"

# Список deploy'ев, которые читают REDIS_PASSWORD из dbos-secrets
# (grep -l REDIS_PASSWORD по k8s/*.yaml даёт ровно эти четыре +
# сам redis-pod). logging-service Redis не использует.
CONSUMERS=(auth-service server-service server-worker secret-service)

ASSUME_YES="false"

# ── Утилиты ───────────────────────────────────────────────────────────────────

confirm() {
    local prompt="$1"
    if [[ "$ASSUME_YES" == "true" ]]; then
        return 0
    fi
    read -p "  ${prompt} [yes/no]: " yn
    [[ "$yn" == "yes" ]]
}

require_bin() {
    command -v "$1" >/dev/null 2>&1 || { echo "ОШИБКА: нужен $1 в PATH." >&2; exit 1; }
}

require_bin kubectl
require_bin jq
require_bin openssl

rand_pw() {
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 32 || true
}

secret_set_string() {
    local key="$1"; shift
    local val="$1"
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' \
        -p "$(jq -n --arg k "$key" --arg v "$val" '{stringData: {($k): $v}}')"
}

# Возвращает текущий пароль из Secret'а (base64-decoded).
secret_get_redis_pw() {
    kubectl -n "$NS" get secret "$SECRET" -o jsonpath='{.data.REDIS_PASSWORD}' \
        | base64 -d
}

show_status() {
    echo ""
    echo "Namespace: $NS"
    echo "Secret:    $SECRET (key REDIS_PASSWORD)"
    echo ""
    local age
    age=$(kubectl -n "$NS" get secret "$SECRET" -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null || true)
    echo "Возраст Secret'а:  ${age:-<нет>}"
    echo ""
    echo "Redis pod:"
    kubectl -n "$NS" get pod -l app=redis -o wide 2>/dev/null | tail -n +1
    echo ""
    echo "Последний rollout redis:"
    local r
    r=$(kubectl -n "$NS" get deploy "$REDIS_DEPLOY" -o jsonpath='{.spec.template.metadata.annotations.kubectl\.kubernetes\.io/restartedAt}' 2>/dev/null || true)
    printf "  %-18s restartedAt=%s\n" "$REDIS_DEPLOY" "${r:-<never>}"
    echo ""
    echo "Consumer'ы (последний restartedAt):"
    for d in "${CONSUMERS[@]}"; do
        local restarted
        restarted=$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.spec.template.metadata.annotations.kubectl\.kubernetes\.io/restartedAt}' 2>/dev/null || true)
        printf "  %-18s restartedAt=%s\n" "$d" "${restarted:-<never>}"
    done
    echo ""
}

# ── Парсинг аргументов ────────────────────────────────────────────────────────

case "${1:-}" in
    --status)
        show_status
        exit 0
        ;;
    --yes|-y)
        ASSUME_YES="true"
        ;;
    "")
        ;;
    *)
        echo "ОШИБКА: неизвестный аргумент: $1" >&2
        echo "Используй: $0 [--status|--yes]" >&2
        exit 1
        ;;
esac

# ── Полный flow ───────────────────────────────────────────────────────────────

echo "=== РОТАЦИЯ REDIS_PASSWORD ==="
show_status

# Step 0: pre-check — текущий пароль из Secret'а должен подходить к живому pod'у.
echo "→ Step 0: pre-check (текущий AUTH работает)..."
if ! kubectl -n "$NS" exec "deploy/${REDIS_DEPLOY}" -- \
        sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping' \
        >/dev/null 2>&1; then
    echo "ОШИБКА: текущий REDIS_PASSWORD из Secret'а не подходит к запущенному redis-pod'у." >&2
    echo "         Сначала разберись с расхождением — иначе ротация ослепит cluster." >&2
    exit 1
fi
echo "  OK (PONG)."

# Generate новый пароль + backup старого.
NEW_PW=$(rand_pw)
if [[ ${#NEW_PW} -ne 32 ]]; then
    echo "ОШИБКА: rand_pw вернул не 32 символа (${#NEW_PW})." >&2
    exit 1
fi

OLD_PW=$(secret_get_redis_pw || true)
if [[ -z "$OLD_PW" ]]; then
    echo "ОШИБКА: не удалось прочитать текущий REDIS_PASSWORD из Secret'а." >&2
    exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/tmp/dbos-rotate-redis-${TS}.txt"
umask 077
{
    echo "DBOS Redis password rotation"
    echo "timestamp: ${TS}"
    echo ""
    echo "OLD REDIS_PASSWORD (для recovery при сбое в середине flow):"
    echo "${OLD_PW}"
    echo ""
    echo "NEW REDIS_PASSWORD:"
    echo "${NEW_PW}"
} > "$OUT"
chmod 600 "$OUT"

cat <<EOF

Будет сделано:
  1. Patch Secret ${SECRET}.REDIS_PASSWORD <new>.
  2. Rolling restart deploy/${REDIS_DEPLOY}  (strategy: Recreate, ~5-15s downtime).
  3. Rolling restart consumer'ов:
       ${CONSUMERS[*]}
  4. kubectl rollout status (redis + consumers).
  5. Verify: redis-cli -a <new> ping → PONG.

Старый и новый пароли сохранены в:
  ${OUT}   (chmod 600)

Новый Redis-пароль:
  ${NEW_PW}

EOF

confirm "Продолжить ротацию?" || { echo "Отменено."; shred -u "$OUT" 2>/dev/null || rm -f "$OUT"; exit 0; }

# Step 1: patch Secret.
echo ""
echo "→ Step 1: patch Secret ${SECRET}.REDIS_PASSWORD..."
if ! secret_set_string "REDIS_PASSWORD" "$NEW_PW"; then
    echo "ОШИБКА: patch Secret провалился. Redis ещё со старым паролем, Secret тоже." >&2
    echo "         Система в исходном состоянии. ${OUT} с неприменённым паролем оставлен." >&2
    exit 1
fi

# Step 2: rolling restart redis. strategy=Recreate в манифесте — старый pod
# умирает до запуска нового, ~5-15s downtime. Это допустимо: redis stash
# у нас транзиентный, очереди taskiq переживут (worker сделает reconnect).
echo ""
echo "→ Step 2: rolling restart deploy/${REDIS_DEPLOY} (Recreate strategy)..."
confirm "Рестартовать deploy/${REDIS_DEPLOY}?" \
    || { echo "→ Pause. Дальше — kubectl -n ${NS} rollout restart deploy/${REDIS_DEPLOY}"; exit 0; }

kubectl -n "$NS" rollout restart "deploy/${REDIS_DEPLOY}"
if ! kubectl -n "$NS" rollout status "deploy/${REDIS_DEPLOY}" --timeout=180s; then
    echo "ОШИБКА: rollout status redis не завершился за 180s." >&2
    echo "         Secret уже с НОВЫМ паролем; pod либо не стартует с новым env," >&2
    echo "         либо stuck на старом. Проверь: kubectl -n ${NS} describe pod -l app=redis" >&2
    exit 1
fi

# Step 3: rolling restart consumer'ов.
echo ""
echo "→ Step 3: rolling restart consumer'ов..."
confirm "Рестартовать ${CONSUMERS[*]}?" \
    || { echo "→ Pause. Запусти руками: kubectl -n ${NS} rollout restart deploy/${CONSUMERS[*]/#/deploy/}"; exit 0; }

for d in "${CONSUMERS[@]}"; do
    kubectl -n "$NS" rollout restart "deploy/${d}"
done
for d in "${CONSUMERS[@]}"; do
    if ! kubectl -n "$NS" rollout status "deploy/${d}" --timeout=300s; then
        echo "ОШИБКА: rollout status ${d} не завершился за 300s." >&2
        echo "         Скорее всего — pod не может авторизоваться в Redis. Логи:" >&2
        echo "           kubectl -n ${NS} logs deploy/${d} --tail=200" >&2
        exit 1
    fi
done

# Step 4: verify connectivity внутри pod'а — берём пароль из env (= новый
# Secret), пингуем сами себя. Это catch-all для случая, когда rollout прошёл,
# но Redis по какой-то причине поднялся со старым env (например, Secret
# не пере-resolve'ился).
echo ""
echo "→ Step 4: verify redis-cli ping..."
if ! kubectl -n "$NS" exec "deploy/${REDIS_DEPLOY}" -- \
        sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping' \
        >/dev/null 2>&1; then
    echo "ОШИБКА: после ротации redis-cli ping провалился. Проверь руками:" >&2
    echo "  kubectl -n ${NS} exec deploy/${REDIS_DEPLOY} -- redis-cli -a \"\$REDIS_PASSWORD\" ping" >&2
    exit 1
fi
echo "  OK (PONG)."

cat <<EOF

✓ Готово.

Что осталось проверить руками:
  kubectl -n ${NS} logs deploy/auth-service     --tail=200 | grep -iE 'redis|auth'   — без NOAUTH/WRONGPASS.
  kubectl -n ${NS} logs deploy/server-worker    --tail=200 | grep -iE 'redis|taskiq' — то же.

Дубликат пароля: ${OUT} (chmod 600).
После переноса в password manager:  shred -u ${OUT}

EOF
