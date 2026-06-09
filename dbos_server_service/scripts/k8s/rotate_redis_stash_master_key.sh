#!/usr/bin/env bash
# Ротация REDIS_STASH_ENCRYPTION_KEY (общий ключ server_service ↔ server_worker).
#
# Redis-stash — короткоживущий контур: server_service шифрует in-flight creds
# (password + ssh_private_key) для dispatch'а в Redis под TTL (обычно <1 часа),
# server_worker их читает и применяет. Persistent-данных под этим ключом
# в БД НЕТ — поэтому после max(TTL) от момента ротации старый ключ больше
# никому не нужен и его можно безопасно дропнуть.
#
# Безопасный finalize:
#   migration_status в этом контуре не применим (нет БД-ciphertext'ов). Вместо
#   него проверяем uptime Redis pod'а с момента (rolling restart server-svc +
#   server-worker) → если прошло >REDIS_STASH_TTL_S (default 3600), все stash'и
#   под старым ключом естественно протухли по TTL. До этого момента finalize
#   откажется работать без --force-finalize.
#
# Использование:
#   scripts/k8s/rotate_redis_stash_master_key.sh                  # фаза 1
#   scripts/k8s/rotate_redis_stash_master_key.sh --finalize       # фаза 2 (TTL прошёл)
#   scripts/k8s/rotate_redis_stash_master_key.sh --auto-finalize  # CronJob
#   scripts/k8s/rotate_redis_stash_master_key.sh --status         # текущее состояние
#
# Флаги:
#   --yes              non-interactive
#   --force-finalize   bypass uptime-проверки (DR/incident)
#
# Требования: kubectl, jq, openssl

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
SERVER_DEPLOY="server-service"
WORKER_DEPLOY="server-worker"
REDIS_DEPLOY="redis"

# Минимум секунд с момента (последнего restart redis или ротации), после
# которого считаем in-flight stash'и под старым ключом протухшими по TTL.
REDIS_STASH_TTL_S="${REDIS_STASH_TTL_S:-3600}"

# Метка-таймстамп ротации, который мы сохраняем в Secret'е после rotate,
# чтобы finalize мог посчитать elapsed относительно неё, а не uptime Redis'а
# (uptime может сбиться, если Redis рестартовали по другой причине).
ROTATE_TS_FIELD="REDIS_STASH_ENCRYPTION_KEY_ROTATED_AT"

# ── Аргументы ─────────────────────────────────────────────────────────────────
ACTION="rotate"
ASSUME_YES="false"
FORCE_FINALIZE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --status)         ACTION="status";         shift ;;
        --finalize)       ACTION="finalize";       shift ;;
        --auto-finalize)  ACTION="auto-finalize";  shift ;;
        --force-finalize) FORCE_FINALIZE="true";   shift ;;
        --yes|-y)         ASSUME_YES="true";       shift ;;
        -h|--help)        sed -n '2,30p' "$0";     exit 0 ;;
        *) echo "ОШИБКА: неизвестный аргумент: $1" >&2; exit 1 ;;
    esac
done

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

# shellcheck source=_rotation_helpers.sh
source "$SCRIPT_DIR/_rotation_helpers.sh"

secret_get() {
    local key="$1"
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r ".data.\"${key}\" // empty" \
        | { local b64; b64=$(cat); [[ -n "$b64" ]] && echo "$b64" | base64 -d || true; }
}

secret_set_string() {
    local key="$1"; shift
    local val="$1"
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' \
        -p "$(jq -n --arg k "$key" --arg v "$val" '{stringData: {($k): $v}}')"
}

secret_unset() {
    local key="$1"
    kubectl -n "$NS" patch secret "$SECRET" --type='json' \
        -p "[{\"op\":\"remove\",\"path\":\"/data/${key}\"}]" 2>/dev/null || true
}

list_legacy_keys() {
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^REDIS_STASH_ENCRYPTION_KEY__v[0-9]+$' \
        | sort -t v -k2 -n || true
}

# Сколько секунд прошло с последней ротации (по нашей собственной метке).
# Если метки нет — возвращаем огромное число (считаем, что давно).
seconds_since_rotation() {
    local ts_str
    ts_str=$(secret_get "$ROTATE_TS_FIELD" || true)
    if [[ -z "$ts_str" ]]; then
        echo "999999"
        return 0
    fi
    local rotated_at now
    # date -d на BusyBox alpine понимает только ISO8601 в специфичном формате;
    # сохраняем как `date -u +%s` (epoch) — gnarly но переносимо.
    rotated_at="$ts_str"
    now=$(date -u +%s)
    if [[ "$rotated_at" =~ ^[0-9]+$ ]]; then
        echo $(( now - rotated_at ))
    else
        # Fallback: ISO8601 → epoch через date(1). Не работает в BusyBox для
        # произвольных форматов; держим ради backward-compat.
        if command -v date >/dev/null && date -u -d "$rotated_at" +%s >/dev/null 2>&1; then
            rotated_at=$(date -u -d "$rotated_at" +%s)
            echo $(( now - rotated_at ))
        else
            echo "999999"
        fi
    fi
}

show_status() {
    local cur ver elapsed
    cur=$(secret_get REDIS_STASH_ENCRYPTION_KEY || true)
    ver=$(secret_get REDIS_STASH_ENCRYPTION_KEY_VERSION || true)
    echo ""
    echo "Namespace:           $NS"
    echo "Secret:              $SECRET"
    echo "Текущая версия:      ${ver:-<нет>}"
    echo "REDIS_STASH_ENCRYPTION_KEY length: ${#cur} bytes"
    echo ""
    echo "Previous-keys в Secret'е (если есть):"
    list_legacy_keys | sed 's/^/  /' || echo "  (нет)"
    echo ""
    elapsed=$(seconds_since_rotation)
    echo "Прошло с последней ротации: ${elapsed}s (требуется ≥${REDIS_STASH_TTL_S}s для безопасного finalize)"
    echo ""
}

# Проверка: можно ли безопасно дропать __v<old>? Да, если elapsed > TTL.
check_ttl_expired() {
    local elapsed
    elapsed=$(seconds_since_rotation)
    if [[ "$elapsed" -ge "$REDIS_STASH_TTL_S" ]]; then
        return 0
    fi
    local wait_more=$(( REDIS_STASH_TTL_S - elapsed ))
    echo "ОШИБКА: с момента ротации прошло ${elapsed}s, нужно ≥${REDIS_STASH_TTL_S}s." >&2
    echo "  Подожди ещё ${wait_more}s, чтобы in-flight stash'и под старым ключом протухли." >&2
    return 1
}

# ── ACTION: status ────────────────────────────────────────────────────────────

if [[ "$ACTION" == "status" ]]; then
    show_status
    exit 0
fi

# ── ACTION: finalize / auto-finalize ──────────────────────────────────────────

do_finalize() {
    echo "=== FINALIZE REDIS_STASH_ENCRYPTION_KEY ==="
    echo ""
    show_status

    local prev_keys
    prev_keys=$(list_legacy_keys)
    if [[ -z "$prev_keys" ]]; then
        echo "→ Previous-keys уже не в Secret'е. Готово."
        # Заодно подчистим устаревшую метку.
        secret_unset "$ROTATE_TS_FIELD"
        return 0
    fi

    if [[ "$FORCE_FINALIZE" == "true" ]]; then
        confirm_force_finalize "redis-stash-master" || { echo "Отменено."; exit 1; }
    else
        if ! check_ttl_expired; then
            exit 1
        fi
        echo "✓ TTL-цикл прошёл, in-flight stash'и под старым ключом протухли."
        echo ""
        confirm "Удалить ВСЕ previous-keys из Secret'а?" || { echo "Отменено."; exit 0; }
    fi

    while read -r key; do
        [[ -z "$key" ]] && continue
        echo "→ Удаляю $key из Secret'а..."
        secret_unset "$key"
    done <<< "$prev_keys"

    secret_unset "$ROTATE_TS_FIELD"

    echo "→ Rolling restart server-service + server-worker..."
    kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
    kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
    kubectl -n "$NS" rollout status  deploy/"$SERVER_DEPLOY" --timeout=300s
    kubectl -n "$NS" rollout status  deploy/"$WORKER_DEPLOY" --timeout=300s

    echo ""
    echo "✓ Финализация завершена. Previous-keys удалены."
}

drop_legacy_older_than() {
    local keep_ver="$1"
    local key ver
    while read -r key; do
        [[ -z "$key" ]] && continue
        ver="${key##*__v}"
        if [[ "$ver" -lt "$keep_ver" ]]; then
            echo "→ Удаляю $key (старее __v${keep_ver})..."
            secret_unset "$key"
        fi
    done <<< "$(list_legacy_keys)"
}

if [[ "$ACTION" == "finalize" ]]; then
    do_finalize
    exit 0
fi

# ── ACTION: rotate ────────────────────────────────────────────────────────────

do_rotate() {
    local cur_key cur_ver new_ver new_key
    cur_key=$(secret_get REDIS_STASH_ENCRYPTION_KEY)
    cur_ver=$(secret_get REDIS_STASH_ENCRYPTION_KEY_VERSION)

    if [[ -z "$cur_key" || -z "$cur_ver" ]]; then
        echo "ОШИБКА: не нашёл REDIS_STASH_ENCRYPTION_KEY / _VERSION в Secret'е $NS/$SECRET." >&2
        exit 1
    fi

    new_ver=$((cur_ver + 1))
    new_key=$(openssl rand -base64 32 | tr -d '\n=')

    local ts summary_out now_epoch
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    now_epoch="$(date -u +%s)"
    summary_out="/tmp/dbos-rotate-redis-stash-${ts}.txt"

    cat <<EOF

Будет сделано:
  1. Текущий ключ (v${cur_ver}) переедет в REDIS_STASH_ENCRYPTION_KEY__v${cur_ver}.
  2. REDIS_STASH_ENCRYPTION_KEY = <новый>, VERSION = ${new_ver}.
  3. Метка ${ROTATE_TS_FIELD} = ${now_epoch} (для finalize gating через TTL).
  4. Rolling restart server-service + server-worker.
  5. Finalize возможен после ≥${REDIS_STASH_TTL_S}s от now (TTL-цикл).

EOF

    if [[ "$ASSUME_YES" != "true" ]]; then
        cat <<EOF
Новый мастер-ключ (СОХРАНИ — без него восстановление невозможно):
  ${new_key}

Дубликат в: ${summary_out}

EOF
        {
            echo "DBOS redis-stash master-key rotation"
            echo "timestamp: ${ts}"
            echo "old_version: ${cur_ver}"
            echo "new_version: ${new_ver}"
            echo ""
            echo "NEW REDIS_STASH_ENCRYPTION_KEY:"
            echo "${new_key}"
            echo ""
            echo "PREVIOUS REDIS_STASH_ENCRYPTION_KEY (теперь под REDIS_STASH_ENCRYPTION_KEY__v${cur_ver}):"
            echo "${cur_key}"
        } > "$summary_out"
        chmod 600 "$summary_out"
    fi

    confirm "Продолжить ротацию?" || {
        echo "Отменено."
        [[ -f "$summary_out" ]] && { shred -u "$summary_out" 2>/dev/null || rm -f "$summary_out"; }
        return 1
    }

    echo ""
    echo "→ Пишу previous-ключ в REDIS_STASH_ENCRYPTION_KEY__v${cur_ver}..."
    secret_set_string "REDIS_STASH_ENCRYPTION_KEY__v${cur_ver}" "$cur_key"

    echo "→ Подменяю REDIS_STASH_ENCRYPTION_KEY/VERSION + ставлю метку ротации..."
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' -p "$(jq -n \
        --arg new_key "$new_key" \
        --arg new_ver "$new_ver" \
        --arg ts_field "$ROTATE_TS_FIELD" \
        --arg ts_val "$now_epoch" \
        '{stringData: {REDIS_STASH_ENCRYPTION_KEY: $new_key,
                       REDIS_STASH_ENCRYPTION_KEY_VERSION: $new_ver,
                       ($ts_field): $ts_val}}')"

    echo ""
    echo "→ Rolling restart server-service + server-worker..."
    confirm "Рестартовать сейчас?" || {
        echo "Pause. Запусти руками: kubectl -n $NS rollout restart deploy/$SERVER_DEPLOY deploy/$WORKER_DEPLOY"
        return 0
    }

    kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
    kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
    kubectl -n "$NS" rollout status  deploy/"$SERVER_DEPLOY" --timeout=300s
    kubectl -n "$NS" rollout status  deploy/"$WORKER_DEPLOY" --timeout=300s

    cat <<EOF

✓ Ключ ротирован. Активная версия = v${new_ver}.

Подожди ≥${REDIS_STASH_TTL_S}s (TTL-цикл in-flight stash'ей под v${cur_ver}),
потом финализируй:
    $0 --finalize

EOF
}

if [[ "$ACTION" == "rotate" ]]; then
    echo "=== РОТАЦИЯ REDIS_STASH_ENCRYPTION_KEY (фаза 1) ==="
    show_status
    do_rotate
    exit 0
fi

# ── ACTION: auto-finalize ──────────────────────────────────────────────────────

if [[ "$ACTION" == "auto-finalize" ]]; then
    echo "=== AUTO-FINALIZE REDIS_STASH_ENCRYPTION_KEY ==="
    show_status

    cur_ver=$(secret_get REDIS_STASH_ENCRYPTION_KEY_VERSION)
    if [[ -z "$cur_ver" ]]; then
        echo "ОШИБКА: REDIS_STASH_ENCRYPTION_KEY_VERSION пуст в Secret'е." >&2
        exit 1
    fi

    legacy=$(list_legacy_keys || true)

    if [[ -n "$legacy" ]]; then
        if check_ttl_expired 2>/dev/null; then
            echo "✓ TTL прошёл → finalize."
            while read -r key; do
                [[ -z "$key" ]] && continue
                echo "→ Удаляю $key..."
                secret_unset "$key"
            done <<< "$legacy"
            secret_unset "$ROTATE_TS_FIELD"
            echo "→ Rolling restart..."
            kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
            kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
            kubectl -n "$NS" rollout status  deploy/"$SERVER_DEPLOY" --timeout=300s
            kubectl -n "$NS" rollout status  deploy/"$WORKER_DEPLOY" --timeout=300s
        else
            echo "⚠ TTL ещё не прошёл — finalize пропускается."
            echo "  Дроплю только __v<N-2> и старее (если есть): они уже точно протухли"
            echo "  за прошлый CronJob-цикл (6 мес)."
            drop_legacy_older_than "$cur_ver"
        fi
    else
        echo "→ Legacy keys в Secret'е нет — finalize не требуется."
    fi

    echo ""
    echo "=== ROTATE → новая версия ==="
    do_rotate

    echo ""
    echo "✓ auto-finalize завершён."
    exit 0
fi

echo "ОШИБКА: неизвестное действие: $ACTION" >&2
exit 1
