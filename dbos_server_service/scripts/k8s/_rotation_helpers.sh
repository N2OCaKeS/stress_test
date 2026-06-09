#!/usr/bin/env bash
# Общие helper'ы для rotate_*.sh скриптов:
#   - confirm / require_bin / rand_alnum — базовые утилиты,
#   - secret_get / secret_set_string / secret_unset — обёртки над kubectl patch
#     Secret'а dbos-secrets,
#   - fetch_migration_status_json / migration_status_is_complete — проверка
#     /internal/migration_status для master-key ротаторов.
#
# Не запускается напрямую — source'ится из rotate_master_key.sh,
# rotate_secret_master_key.sh, rotate_redis_stash_master_key.sh,
# rotate_db_passwords.sh, rotate_redis_password.sh, rotate_s2s_keys.sh.
#
# Контракт по env-переменным (выставляет caller перед source'ом):
#   NS                  — namespace (обычно $DBOS_NAMESPACE / dbos)
#   SECRET              — имя Secret'а (обычно $DBOS_SECRET_NAME / dbos-secrets)
#   ASSUME_YES          — "true" → confirm() возвращает 0 без prompt'а

# shellcheck shell=bash

# ── Базовые утилиты (раньше дублировались в каждом rotate_*.sh) ───────────────

# Подтверждение действия. Если ASSUME_YES=true — auto-yes (CronJob / harness).
confirm() {
    local prompt="$1"
    if [[ "${ASSUME_YES:-false}" == "true" ]]; then
        return 0
    fi
    read -p "  ${prompt} [yes/no]: " yn
    [[ "$yn" == "yes" ]]
}

# Проверить, что бинарь доступен; иначе fail с понятным сообщением.
require_bin() {
    command -v "$1" >/dev/null 2>&1 || { echo "ОШИБКА: нужен $1 в PATH." >&2; exit 1; }
}

# Генератор alphanumeric строки заданной длины (gen_secrets.sh::rand-формат).
# Используется для DB-паролей (32), Redis-пароля (32), S2S-ключей (48).
rand_alnum() {
    local n="$1"
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "$n" || true
}

# ── kubectl Secret обёртки ────────────────────────────────────────────────────

# Прочитать одно поле Secret'а (base64-decoded). Пустую строку при отсутствии.
secret_get() {
    local key="$1"
    kubectl -n "$NS" get secret "$SECRET" -o json 2>/dev/null \
        | jq -r ".data.\"${key}\" // empty" \
        | { local b64; b64=$(cat); [[ -n "$b64" ]] && echo "$b64" | base64 -d || true; }
}

# Пропатчить одно поле через stringData (kubectl сам base64-енкодит).
secret_set_string() {
    local key="$1"; shift
    local val="$1"
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' \
        -p "$(jq -n --arg k "$key" --arg v "$val" '{stringData: {($k): $v}}')"
}

# Удалить поле из Secret'а (JSON-patch). Тихо игнорирует «нет такого поля».
secret_unset() {
    local key="$1"
    kubectl -n "$NS" patch secret "$SECRET" --type='json' \
        -p "[{\"op\":\"remove\",\"path\":\"/data/${key}\"}]" 2>/dev/null || true
}

# ── Identity для запроса к /internal/migration_status ─────────────────────────
#
# Параллельные lazy-агенты добавляют GET /api/{secret,server}/v1/internal/migration_status,
# защищённый require_internal_caller (X-Service-Identity + Bearer из *_INBOUND_SERVICE_API_KEYS).
# Используем фиксированную identity `rotation_runner`. Оператор должен
# добавить запись `rotation_runner:<random_key>` в SECRET_INBOUND_SERVICE_API_KEYS
# и SERVER_INBOUND_SERVICE_API_KEYS — это делается один раз на установку.
#
# Скрипт читает Bearer из Secret'а dbos-secrets, поле
# ROTATION_RUNNER_API_KEY. Если поля нет — скрипт сообщит, как его создать,
# и (для --finalize) откажется дропать ключ.
ROTATION_RUNNER_IDENTITY="rotation_runner"
ROTATION_RUNNER_KEY_FIELD="ROTATION_RUNNER_API_KEY"

# ── Достаём rotation_runner API key из Secret'а ───────────────────────────────
_rotation_runner_key() {
    local val
    val=$(kubectl -n "$NS" get secret "$SECRET" -o json 2>/dev/null \
        | jq -r ".data.\"${ROTATION_RUNNER_KEY_FIELD}\" // empty" \
        | { local b64; b64=$(cat); [[ -n "$b64" ]] && echo "$b64" | base64 -d || true; })
    [[ -n "$val" ]] && echo "$val" || return 1
}

# Печать инструкции по добавлению ROTATION_RUNNER_API_KEY (один раз на установку).
print_rotation_runner_setup_help() {
    cat >&2 <<'EOF'
ОШИБКА: в Secret'е dbos-secrets нет поля ROTATION_RUNNER_API_KEY.

migration_status — internal endpoint, защищённый X-Service-Identity + Bearer.
Чтобы master-key ротатор мог проверить, завершена ли lazy re-encrypt миграция,
заведи identity rotation_runner в inbound map'ах сервисов:

  KEY=$(openssl rand -hex 32)
  kubectl -n dbos patch secret dbos-secrets --type='merge' -p "$(jq -n --arg k "$KEY" '
      {stringData: {
          ROTATION_RUNNER_API_KEY: $k
      }}')"

  # Добавь rotation_runner:$KEY в SECRET_INBOUND_SERVICE_API_KEYS и
  # SERVER_INBOUND_SERVICE_API_KEYS. Формат — "name1:key1,name2:key2,...":

  CUR=$(kubectl -n dbos get secret dbos-secrets -o json \
      | jq -r '.data.SECRET_INBOUND_SERVICE_API_KEYS' | base64 -d)
  NEW="${CUR},rotation_runner:${KEY}"
  kubectl -n dbos patch secret dbos-secrets --type='merge' \
      -p "$(jq -n --arg v "$NEW" '{stringData: {SECRET_INBOUND_SERVICE_API_KEYS: $v}}')"
  # то же самое для SERVER_INBOUND_SERVICE_API_KEYS

  # Rolling restart затронутых сервисов, чтобы они подобрали новую map:
  kubectl -n dbos rollout restart deploy/secret-service deploy/server-service

После этого --finalize и --auto-finalize смогут проверять migration_status.
EOF
}

# ── HTTP request → migration_status JSON ───────────────────────────────────────
#
# fetch_migration_status_json <deploy> <port> <api_path>
#   <deploy>   — например, server-service / secret-service
#   <port>     — внутрипоодовый порт (8002 / 8003)
#   <api_path> — путь, начиная с /api/...
#
# Возвращает на stdout JSON; на ошибке — 1 и stderr-сообщение.
fetch_migration_status_json() {
    local deploy="$1"
    local port="$2"
    local path="$3"

    local key
    if ! key=$(_rotation_runner_key); then
        print_rotation_runner_setup_help
        return 1
    fi

    # exec curl с loopback — TLS не нужен, request никогда не покидает pod.
    # -sS: silent + show errors. -f: HTTP errors → exit 22. --max-time 10:
    # endpoint read-only, должен ответить мгновенно.
    local resp
    if ! resp=$(kubectl -n "$NS" exec "deploy/${deploy}" -c "${deploy}" -- \
            curl -sS -f --max-time 10 \
            -H "X-Service-Identity: ${ROTATION_RUNNER_IDENTITY}" \
            -H "Authorization: Bearer ${key}" \
            "http://localhost:${port}${path}" 2>&1); then
        echo "ОШИБКА: запрос ${path} к deploy/${deploy} провалился: ${resp}" >&2
        return 1
    fi
    echo "$resp"
}

# ── Проверка remaining_legacy == 0 ─────────────────────────────────────────────
#
# Парсит ответ migration_status и говорит, можно ли финализировать.
# Поддерживает оба формата (на случай drift между server и secret):
#   {"remaining_legacy": N, ...}
#   {"remaining": N, "outbox": {"pending": N, "processing": N}, ...}
#   {"migrated_pct": 100.0, ...}
#
# migration_status_is_complete <json> → exit 0 если завершена, 1 если нет.
# При экзите 1 stdout — короткое сообщение "N rows on legacy key".
migration_status_is_complete() {
    local json="$1"

    # 1. migrated_pct
    local pct
    pct=$(echo "$json" | jq -r '.migrated_pct // empty' 2>/dev/null || true)
    if [[ -n "$pct" && "$pct" != "null" ]]; then
        # Сравнение float'ов: 100 == 100.0; awk надёжнее, чем bash arithmetic.
        if awk "BEGIN{exit !($pct >= 100.0)}"; then
            return 0
        fi
        echo "migrated_pct=${pct} (<100)"
        return 1
    fi

    # 2. remaining_legacy / remaining (+ outbox.pending/processing).
    local remaining outbox_pending outbox_processing
    remaining=$(echo "$json" | jq -r '.remaining_legacy // .remaining // empty' 2>/dev/null || true)
    outbox_pending=$(echo "$json"     | jq -r '.outbox.pending    // 0' 2>/dev/null || echo 0)
    outbox_processing=$(echo "$json"  | jq -r '.outbox.processing // 0' 2>/dev/null || echo 0)

    if [[ -z "$remaining" || "$remaining" == "null" ]]; then
        echo "migration_status: не нашёл ни migrated_pct, ни remaining(_legacy) в ответе:" >&2
        echo "$json" | head -c 500 >&2
        echo "" >&2
        return 1
    fi

    if [[ "$remaining" == "0" && "$outbox_pending" == "0" && "$outbox_processing" == "0" ]]; then
        return 0
    fi
    echo "remaining_legacy=${remaining} outbox.pending=${outbox_pending} outbox.processing=${outbox_processing}"
    return 1
}

# ── --force-finalize интерактивная проверка ────────────────────────────────────
#
# Используется вместе с migration_status_is_complete, когда пользователь
# передал --force-finalize. Под --yes (ASSUME_YES=true) сразу пропускает
# без интерактивного подтверждения — но только если в env DBOS_FORCE_FINALIZE=1.
# Иначе под CronJob force-режим запрещён, чтобы случайный --force-finalize
# из rotate-args не превратился в data-loss.
confirm_force_finalize() {
    local ctx="$1"
    if [[ "${ASSUME_YES:-false}" == "true" ]]; then
        if [[ "${DBOS_FORCE_FINALIZE:-0}" == "1" ]]; then
            echo "→ --force-finalize: ASSUME_YES + DBOS_FORCE_FINALIZE=1 — bypass approved (${ctx})."
            return 0
        fi
        echo "ОШИБКА: --force-finalize требует интерактивного подтверждения." >&2
        echo "  В non-interactive режиме передай DBOS_FORCE_FINALIZE=1 явно." >&2
        return 1
    fi
    echo ""
    echo "  !!! --force-finalize bypass'ит проверку migration_status."
    echo "  !!! Если миграция не завершена — оставшиеся ciphertext'ы под старым"
    echo "  !!! ключом станут необратимо недешифруемыми после удаления __v<old>."
    echo "  !!! Использовать только при DR / incident-response."
    echo ""
    read -p "  Точно продолжить force-finalize ${ctx}? [type 'force' to confirm]: " ans
    [[ "$ans" == "force" ]]
}
