#!/usr/bin/env bash
# Ротация Redis-пароля без потери доступа.
#
# Redis запущен с `--requirepass $(REDIS_PASSWORD)` из env (см. k8s/12-redis.yaml).
# В PVC лежит только AOF (`/data/appendonly.aof`), redis.conf не используется.
# Поэтому:
#   - CONFIG SET requirepass меняет пароль live, существующие connection'ы
#     остаются auth'енчены (Redis их не разрывает).
#   - CONFIG REWRITE НЕ НУЖЕН — нет файла-конфига на диске, writeable conf path
#     не задан, и REWRITE упадёт с "The server is running without a config file".
#     При следующем рестарте Redis перечитает env REDIS_PASSWORD из Secret'а,
#     который мы тут же патчим.
#
# Flow:
#   1. Сгенерировать новый пароль (32 символа [A-Za-z0-9]).
#   2. redis-cli CONFIG SET requirepass <new>  внутри pod'а.
#   3. Sanity: AUTH <new> внутри того же pod'а (быстрый smoke).
#   4. Patch Secret dbos-secrets.REDIS_PASSWORD.
#   5. Rolling restart всех consumer'ов, читающих REDIS_PASSWORD из env:
#         auth-service, logging-service, server-service, server-worker, secret-service.
#      Они подхватят новый Secret и откроют новые connection'ы с новым AUTH.
#   6. (Опционально) удалить старые connection'ы, висящие со старым AUTH —
#      `redis-cli CLIENT KILL` по filter'у. Сейчас пропускаем: pods'ы уже
#      рестартнулись, старых клиентов быть не должно.
#
# Скрипт ИНТЕРАКТИВНЫЙ.
#
# Использование:
#   scripts/k8s/rotate_redis_password.sh           # полный flow
#   scripts/k8s/rotate_redis_password.sh --status  # текущее состояние
#
# Требования: kubectl, jq, openssl.

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
REDIS_DEPLOY="redis"

# Список deploy'ев, которые читают REDIS_PASSWORD из dbos-secrets (см. grep -l
# REDIS_PASSWORD по k8s/*.yaml).
CONSUMERS=(auth-service logging-service server-service server-worker secret-service)

# ── Утилиты ───────────────────────────────────────────────────────────────────

confirm() {
    local prompt="$1"
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
    echo "Consumer'ы (последний restartedAt):"
    for d in "${CONSUMERS[@]}"; do
        local restarted
        restarted=$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.spec.template.metadata.annotations.kubectl\.kubernetes\.io/restartedAt}' 2>/dev/null || true)
        printf "  %-18s restartedAt=%s\n" "$d" "${restarted:-<never>}"
    done
    echo ""
    echo "Подсказка: текущий пароль (если нужно проверить вручную):"
    echo "  kubectl -n ${NS} get secret ${SECRET} -o jsonpath='{.data.REDIS_PASSWORD}' | base64 -d; echo"
    echo ""
}

# ── --status ──────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "--status" ]]; then
    show_status
    exit 0
fi

if [[ "${1:-}" != "" ]]; then
    echo "ОШИБКА: неизвестный аргумент: $1" >&2
    echo "Используй: $0 [--status]" >&2
    exit 1
fi

# ── Полный flow ───────────────────────────────────────────────────────────────

echo "=== РОТАЦИЯ REDIS_PASSWORD ==="
show_status

# Step 0: убедиться, что redis pod жив и текущий пароль вообще валиден
# (на случай если Secret и pod уже разъехались).
echo "→ Step 0: проверка текущего AUTH..."
if ! kubectl -n "$NS" exec "deploy/${REDIS_DEPLOY}" -- \
        sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping' \
        >/dev/null 2>&1; then
    echo "ОШИБКА: текущий REDIS_PASSWORD из Secret'а не подходит к запущенному redis-pod'у." >&2
    echo "         Сначала разберись с расхождением — иначе ротация ослепит cluster." >&2
    exit 1
fi
echo "  OK (PONG)."

NEW_PW=$(rand_pw)
if [[ ${#NEW_PW} -ne 32 ]]; then
    echo "ОШИБКА: rand_pw вернул не 32 символа (${#NEW_PW})." >&2
    exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/tmp/dbos-rotate-redis-${TS}.txt"
{
    echo "DBOS Redis password rotation"
    echo "timestamp: ${TS}"
    echo ""
    echo "NEW REDIS_PASSWORD:"
    echo "${NEW_PW}"
} > "$OUT"
chmod 600 "$OUT"

cat <<EOF

Будет сделано:
  1. CONFIG SET requirepass <new> внутри redis-pod (без рестарта самого Redis).
  2. AUTH <new> sanity внутри pod'а.
  3. Patch Secret ${SECRET}.REDIS_PASSWORD.
  4. Rolling restart consumer'ов:
       ${CONSUMERS[*]}
  5. (CONFIG REWRITE не выполняется — Redis запущен без conf-файла,
      перезапуск Redis заберёт пароль из env REDIS_PASSWORD, который
      мы патчим на шаге 3.)

Новый Redis-пароль (СОХРАНИ — без него re-deploy потеряет доступ к AOF
до следующего CONFIG SET):

  REDIS_PASSWORD:
  ${NEW_PW}

Дубликат: ${OUT}

EOF

confirm "Продолжить ротацию?" || { echo "Отменено."; shred -u "$OUT" 2>/dev/null || rm -f "$OUT"; exit 0; }

# Step 1: CONFIG SET requirepass <new>. Передаём через stdin, чтобы пароль
# не попал в kubectl exec args / audit log. redis-cli при пустом аргументе
# заходит в interactive REPL; чтобы он принял команду из stdin, используем
# `-x` (читает последний аргумент из stdin) — но `-x` подставляет stdin как
# единственный аргумент команды, что нам и нужно для requirepass <value>.
echo ""
echo "→ Step 1: CONFIG SET requirepass <new> в redis-pod..."
if ! printf '%s' "$NEW_PW" | kubectl -n "$NS" exec -i "deploy/${REDIS_DEPLOY}" -- \
        sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning -x CONFIG SET requirepass'; then
    echo "ОШИБКА: CONFIG SET requirepass провалился. Redis остался со старым паролем." >&2
    echo "         Secret не тронут — система в исходном состоянии. ${OUT} с новым (неприменённым) паролем не удалён." >&2
    exit 1
fi

# Step 2: sanity — AUTH с новым паролем.
echo "→ Step 2: AUTH <new> sanity..."
if ! printf 'AUTH %s\nPING\n' "$NEW_PW" | kubectl -n "$NS" exec -i "deploy/${REDIS_DEPLOY}" -- \
        redis-cli --no-auth-warning >/dev/null; then
    echo "ОШИБКА: AUTH <new> не прошёл. CONFIG SET prima facie сработал, но новый AUTH не принимается." >&2
    echo "         Чтобы откатить, исполни в redis-pod'е:" >&2
    echo "           redis-cli -a <старый из Secret'а> CONFIG SET requirepass <старый>" >&2
    exit 1
fi
echo "  OK."

# Step 3: patch Secret. С этого момента новый pod, поднявшийся на
# REDIS_PASSWORD из Secret'а, получит новый пароль; старые pod'ы продолжат
# работать на старых open connection'ах до их рестарта.
echo ""
echo "→ Step 3: patch Secret ${SECRET}.REDIS_PASSWORD..."
if ! secret_set_string "REDIS_PASSWORD" "$NEW_PW"; then
    echo "ОШИБКА: patch Secret провалился. Redis уже с НОВЫМ паролем, Secret — со СТАРЫМ." >&2
    echo "         Если consumer rollout'ы рестартнутся сейчас (любая причина) — auth-failure." >&2
    echo "         Запатчь Secret руками (пароль в ${OUT}):" >&2
    echo "           kubectl -n ${NS} patch secret ${SECRET} --type=merge -p '{\"stringData\":{\"REDIS_PASSWORD\":\"<пароль>\"}}'" >&2
    exit 1
fi

# Step 4: rolling restart consumer'ов.
echo ""
echo "→ Step 4: rolling restart consumer'ов..."
confirm "Рестартовать ${CONSUMERS[*]}?" \
    || { echo "→ Pause. Запусти руками: kubectl -n ${NS} rollout restart deploy/${CONSUMERS[*]/#/deploy/}"; exit 0; }

for d in "${CONSUMERS[@]}"; do
    kubectl -n "$NS" rollout restart "deploy/${d}"
done
for d in "${CONSUMERS[@]}"; do
    kubectl -n "$NS" rollout status "deploy/${d}" --timeout=300s
done

cat <<EOF

✓ Готово.

Что осталось проверить:
  - kubectl -n ${NS} logs deploy/auth-service     | grep -iE 'redis|auth'   — не должно быть 'NOAUTH'/'WRONGPASS'.
  - kubectl -n ${NS} logs deploy/server-worker    | grep -iE 'redis|taskiq' — то же.

Дубликат пароля: ${OUT} (chmod 600).
После переноса в password manager:  shred -u ${OUT}

EOF
