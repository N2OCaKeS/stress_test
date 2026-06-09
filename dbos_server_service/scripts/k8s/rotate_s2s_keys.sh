#!/usr/bin/env bash
# Ротация service-to-service ключей dbos-secrets.
#
# Список ключей (см. scripts/k8s/gen_secrets.sh и k8s/20-secrets.yaml.example) —
# всё, что НЕ admin / НЕ DB-password / НЕ encryption-master:
#
#   LOGGING_SERVICE_API_KEY               outbound (auth → loging)
#   LOGGING_SERVICE_API_KEY_SECRET        outbound (secret → loging)
#   LOGGING_SERVICE_API_KEYS_JSON         inbound map  (loging)
#   LOGGING_INTROSPECT_SERVICE_API_KEY    outbound (loging → auth /introspect)
#   SERVICE_API_KEY                       legacy fallback
#   SERVER_SERVICE_API_KEY                outbound (server → auth /introspect)
#   SERVER_INBOUND_SERVICE_API_KEYS       inbound kv-list (server) — worker_bot
#   WORKER_SERVICE_API_KEY                outbound (worker → auth /introspect)
#   WORKER_BOT_TOKEN                      outbound bot-PAT (worker → server /internal/*)
#   SECRET_INTROSPECT_SERVICE_API_KEY     outbound (secret → auth /introspect)
#   SECRET_INBOUND_SERVICE_API_KEYS       inbound JSON map (secret)
#   SECRET_INTERNAL_API_KEY               outbound (auth → secret /internal/*)
#                                         (== ключ auth_service в SECRET inbound map)
#
# НЕ ТРОГАЕМ:
#   AUTH_DB_*, LOGGING_DB_*, SERVER_DB_*, WORKER_DB_*, SECRET_DB_*  → отдельный скрипт
#   REDIS_PASSWORD                                                  → отдельный скрипт
#   SERVER_ENCRYPTION_KEY*, HKDF_SALT_HEX                           → rotate_master_key.sh
#   SECRET_ENCRYPTION_KEY*                                          → rotate_secret_master_key.sh (параллельный агент)
#   REDIS_STASH_ENCRYPTION_KEY*                                     → rotate_redis_stash_master_key.sh (параллельный агент)
#   AUTH_SECRET_KEY (JWT signing)                                   → не sscope этого скрипта
#   DOCKER_RSA_PRIVATE_KEY                                          → отдельный flow
#   INITIAL_ADMIN_*                                                 → bootstrap, ротация через UI
#
# Так как inbound и outbound ключи парные (LOGGING_SERVICE_API_KEY[caller] ==
# LOGGING_SERVICE_API_KEYS_JSON.<caller>), их надо менять одновременно и одним
# patch'ем — иначе между ALTER'ом outbound и inbound получится window'а
# 401 Unauthorized. Поэтому собираем ВЕСЬ JSON-патч и применяем ОДИН раз.
#
# После patch'а — rolling restart ВСЕХ service-deploy'ев (они и callers,
# и callees) одновременно. Возможна короткая window'а 401/403 пока pod'ы
# поднимаются — это допустимо (synchronous request retry на стороне callers).
#
# Скрипт ИНТЕРАКТИВНЫЙ.
#
# Использование:
#   scripts/k8s/rotate_s2s_keys.sh             # полный flow
#   scripts/k8s/rotate_s2s_keys.sh --dry-run   # показать список ключей и diff Secret'а, не применяя
#   scripts/k8s/rotate_s2s_keys.sh --status    # возраст Secret'а + restartedAt consumer'ов
#
# Требования: kubectl, jq, openssl.

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"

# Все deploy'и, которые в любом качестве (caller или callee) используют s2s-ключи.
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

# 48 символов [A-Za-z0-9] — длина, которой пользуется gen_secrets.sh для
# s2s-ключей (rand 48). Бот-токен — dbos_bot_<rand 48> (префикс отдельно).
rand_key() {
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 48 || true
}

show_status() {
    echo ""
    echo "Namespace: $NS"
    echo "Secret:    $SECRET"
    echo ""
    local age
    age=$(kubectl -n "$NS" get secret "$SECRET" -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null || true)
    echo "Возраст Secret'а: ${age:-<нет>}"
    echo ""
    echo "S2S-ключи в Secret'е (только имена, значения скрыты):"
    kubectl -n "$NS" get secret "$SECRET" -o json 2>/dev/null \
        | jq -r '.data | keys[]' \
        | grep -E '^(LOGGING_SERVICE_API_KEY|LOGGING_SERVICE_API_KEY_SECRET|LOGGING_SERVICE_API_KEYS_JSON|LOGGING_INTROSPECT_SERVICE_API_KEY|SERVICE_API_KEY|SERVER_SERVICE_API_KEY|SERVER_INBOUND_SERVICE_API_KEYS|WORKER_SERVICE_API_KEY|WORKER_BOT_TOKEN|SECRET_INTROSPECT_SERVICE_API_KEY|SECRET_INBOUND_SERVICE_API_KEYS|SECRET_INTERNAL_API_KEY)$' \
        | sed 's/^/  /' || echo "  (нет)"
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

MODE=full
case "${1:-}" in
    --status)  show_status; exit 0 ;;
    --dry-run) MODE=dryrun ;;
    "")        MODE=full ;;
    *)
        echo "ОШИБКА: неизвестный аргумент: $1" >&2
        echo "Используй: $0 [--status|--dry-run]" >&2
        exit 1
        ;;
esac

# ── Генерация нового набора ключей ────────────────────────────────────────────

echo "=== РОТАЦИЯ S2S-КЛЮЧЕЙ ==="
if [[ "$MODE" == "dryrun" ]]; then
    echo "Режим: DRY-RUN (Secret не патчится)."
fi
show_status

echo "→ Генерирую новый набор s2s-ключей..."

# Loging inbound map (parallel keys для каждого caller'а)
NEW_LOGING_AUTH=$(rand_key)
NEW_LOGING_SERVER=$(rand_key)
NEW_LOGING_CONFIG=$(rand_key)
NEW_LOGING_WORKER=$(rand_key)
NEW_LOGING_SECRET=$(rand_key)
# Outbound LOGGING_SERVICE_API_KEY — это значение auth_service в inbound map
# (см. gen_secrets.sh: LOGGING_SERVICE_API_KEY="$LOGGING_SERVICE_API_KEY_AUTH").
NEW_LOGING_OUT="$NEW_LOGING_AUTH"
NEW_LOGING_INTROSPECT=$(rand_key)

# server_service / worker
NEW_SERVER_OUT=$(rand_key)
NEW_WORKER_SERVICE_KEY=$(rand_key)
NEW_WORKER_BOT_TOKEN="dbos_bot_$(rand_key)"
# server_service inbound: worker_bot — единственный caller (см. gen_secrets.sh).
NEW_SERVER_INBOUND="worker_bot:${NEW_WORKER_BOT_TOKEN}"

# secret_service
NEW_SECRET_INTROSPECT=$(rand_key)
NEW_SECRET_INBOUND_WORKER=$(rand_key)
NEW_SECRET_INBOUND_AUTH=$(rand_key)
NEW_SECRET_INBOUND_SERVER=$(rand_key)
# auth_service → secret_service /internal/* — Bearer должен == ключу
# auth_service в SECRET inbound map (см. gen_secrets.sh:
# SECRET_INTERNAL_API_KEY="${SECRET_INBOUND_AUTH_KEY}").
NEW_SECRET_INTERNAL="$NEW_SECRET_INBOUND_AUTH"

# Legacy fallback (gen_secrets.sh: rand 48)
NEW_LEGACY_SERVICE_API_KEY=$(rand_key)

# Собираем JSON map'ы как это делает gen_secrets.sh.
NEW_LOGING_JSON=$(jq -cn \
    --arg a "$NEW_LOGING_AUTH" \
    --arg s "$NEW_LOGING_SERVER" \
    --arg c "$NEW_LOGING_CONFIG" \
    --arg w "$NEW_LOGING_WORKER" \
    --arg x "$NEW_LOGING_SECRET" \
    '{auth_service:$a, server_service:$s, config_service:$c, server_worker:$w, secret_service:$x}')

NEW_SECRET_INBOUND_JSON=$(jq -cn \
    --arg w "$NEW_SECRET_INBOUND_WORKER" \
    --arg a "$NEW_SECRET_INBOUND_AUTH" \
    --arg s "$NEW_SECRET_INBOUND_SERVER" \
    '{worker_bot:$w, auth_service:$a, server_service:$s}')

# Полный stringData-патч для kubectl patch secret.
PATCH_BODY=$(jq -n \
    --arg loging_out             "$NEW_LOGING_OUT" \
    --arg loging_out_secret      "$NEW_LOGING_SECRET" \
    --arg loging_json            "$NEW_LOGING_JSON" \
    --arg loging_introspect      "$NEW_LOGING_INTROSPECT" \
    --arg legacy_service_api_key "$NEW_LEGACY_SERVICE_API_KEY" \
    --arg server_out             "$NEW_SERVER_OUT" \
    --arg server_inbound         "$NEW_SERVER_INBOUND" \
    --arg worker_service         "$NEW_WORKER_SERVICE_KEY" \
    --arg worker_bot             "$NEW_WORKER_BOT_TOKEN" \
    --arg secret_introspect      "$NEW_SECRET_INTROSPECT" \
    --arg secret_inbound_json    "$NEW_SECRET_INBOUND_JSON" \
    --arg secret_internal        "$NEW_SECRET_INTERNAL" \
    '{stringData: {
        LOGGING_SERVICE_API_KEY:            $loging_out,
        LOGGING_SERVICE_API_KEY_SECRET:     $loging_out_secret,
        LOGGING_SERVICE_API_KEYS_JSON:      $loging_json,
        LOGGING_INTROSPECT_SERVICE_API_KEY: $loging_introspect,
        SERVICE_API_KEY:                    $legacy_service_api_key,
        SERVER_SERVICE_API_KEY:             $server_out,
        SERVER_INBOUND_SERVICE_API_KEYS:    $server_inbound,
        WORKER_SERVICE_API_KEY:             $worker_service,
        WORKER_BOT_TOKEN:                   $worker_bot,
        SECRET_INTROSPECT_SERVICE_API_KEY:  $secret_introspect,
        SECRET_INBOUND_SERVICE_API_KEYS:    $secret_inbound_json,
        SECRET_INTERNAL_API_KEY:            $secret_internal
    }}')

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/tmp/dbos-rotate-s2s-${TS}.txt"
{
    echo "DBOS s2s-key rotation"
    echo "timestamp: ${TS}"
    echo ""
    echo "--- loging_service ---"
    echo "LOGGING_SERVICE_API_KEY            (= auth_service caller): ${NEW_LOGING_OUT}"
    echo "LOGGING_SERVICE_API_KEY_SECRET     (secret_service caller): ${NEW_LOGING_SECRET}"
    echo "LOGGING_SERVICE_API_KEYS_JSON:     ${NEW_LOGING_JSON}"
    echo "LOGGING_INTROSPECT_SERVICE_API_KEY (loging → auth):         ${NEW_LOGING_INTROSPECT}"
    echo ""
    echo "--- server_service / server_worker ---"
    echo "SERVER_SERVICE_API_KEY  (server → auth /introspect):        ${NEW_SERVER_OUT}"
    echo "SERVER_INBOUND_SERVICE_API_KEYS:                            ${NEW_SERVER_INBOUND}"
    echo "WORKER_SERVICE_API_KEY  (worker → auth /introspect):        ${NEW_WORKER_SERVICE_KEY}"
    echo "WORKER_BOT_TOKEN        (worker → server /internal/*):      ${NEW_WORKER_BOT_TOKEN}"
    echo ""
    echo "--- secret_service ---"
    echo "SECRET_INTROSPECT_SERVICE_API_KEY (secret → auth):          ${NEW_SECRET_INTROSPECT}"
    echo "SECRET_INBOUND_SERVICE_API_KEYS:                            ${NEW_SECRET_INBOUND_JSON}"
    echo "SECRET_INTERNAL_API_KEY (auth → secret /internal/*):        ${NEW_SECRET_INTERNAL}"
    echo ""
    echo "--- legacy ---"
    echo "SERVICE_API_KEY (fallback): ${NEW_LEGACY_SERVICE_API_KEY}"
} > "$OUT"
chmod 600 "$OUT"

cat <<EOF

Будет пропатчено в Secret ${SECRET} (одним merge-patch'ем):
  LOGGING_SERVICE_API_KEY, LOGGING_SERVICE_API_KEY_SECRET, LOGGING_SERVICE_API_KEYS_JSON, LOGGING_INTROSPECT_SERVICE_API_KEY
  SERVICE_API_KEY (legacy fallback)
  SERVER_SERVICE_API_KEY, SERVER_INBOUND_SERVICE_API_KEYS
  WORKER_SERVICE_API_KEY, WORKER_BOT_TOKEN
  SECRET_INTROSPECT_SERVICE_API_KEY, SECRET_INBOUND_SERVICE_API_KEYS, SECRET_INTERNAL_API_KEY

Парность outbound/inbound:
  LOGGING_SERVICE_API_KEY                == LOGGING_SERVICE_API_KEYS_JSON.auth_service
  LOGGING_SERVICE_API_KEY_SECRET         == LOGGING_SERVICE_API_KEYS_JSON.secret_service
  WORKER_BOT_TOKEN                       == SERVER_INBOUND_SERVICE_API_KEYS[worker_bot]
  SECRET_INTERNAL_API_KEY                == SECRET_INBOUND_SERVICE_API_KEYS.auth_service

После patch'а: rolling restart всех 5 сервис-deploy'ев.

Дубликат: ${OUT}
EOF

if [[ "$MODE" == "dryrun" ]]; then
    echo ""
    echo "=== DRY-RUN: stringData-патч (значения замаскированы первыми 4 символами) ==="
    echo "$PATCH_BODY" | jq '.stringData |= with_entries(.value |= (.[:4] + "…"))'
    echo ""
    echo "Полные значения — в ${OUT}. После просмотра:  shred -u ${OUT}"
    exit 0
fi

confirm "Применить patch и рестартовать всех consumer'ов?" \
    || { echo "Отменено."; shred -u "$OUT" 2>/dev/null || rm -f "$OUT"; exit 0; }

# Step 1: patch.
echo ""
echo "→ Step 1: patch Secret ${SECRET} (12 ключей одним merge)..."
if ! kubectl -n "$NS" patch secret "$SECRET" --type='merge' -p "$PATCH_BODY"; then
    echo "ОШИБКА: patch провалился. Secret не тронут. ${OUT} с неприменёнными значениями оставлен." >&2
    exit 1
fi

# Step 2: rolling restart всех.
echo ""
echo "→ Step 2: rolling restart consumer'ов (одновременно)..."
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

Smoke-проверка (оператор сам, без sleep'ов в скрипте):
  kubectl -n ${NS} logs deploy/auth-service --tail=200 | grep -iE '401|unauthorized service|wrong token'
  kubectl -n ${NS} logs deploy/secret-service --tail=200 | grep -iE '401|unauthorized service'
  kubectl -n ${NS} logs deploy/loging-service --tail=200 | grep -iE '401|unauthorized service' 2>/dev/null || \\
    kubectl -n ${NS} logs deploy/logging-service --tail=200 | grep -iE '401|unauthorized service'
  kubectl -n ${NS} logs deploy/server-worker  --tail=200 | grep -iE '401|invalid bot token'

Если что-то даёт 401 — значит этот caller не подхватил новый Secret
(restart не дошёл, или env не пере-resolve'ился). Лечится: ещё один rollout
restart этого deploy'я.

Дубликат ключей: ${OUT} (chmod 600).
После переноса в password manager:  shred -u ${OUT}

EOF
