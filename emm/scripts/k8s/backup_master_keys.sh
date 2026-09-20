#!/usr/bin/env bash
# Backup всех master-ключей DBOS из k8s Secret'а dbos-secrets в шифрованный архив.
#
# Что попадает в backup:
#   - durable keystore-Secret'ы dbos-server-encryption-keys и
#     dbos-secret-encryption-keys целиком (active_version + key_v<N>) —
#     именно там живёт актуальный, ротированный master-материал шифрования
#     server/secret-service при KEYSTORE_BACKEND=k8s. Кладутся в keystores/.
#   - SERVER_ENCRYPTION_KEY (+ _VERSION) + все SERVER_ENCRYPTION_KEY__v<N> legacy
#     (env-seed / file-бэкенд; на проде сам по себе уже не активен)
#   - SECRET_ENCRYPTION_KEY (+ _VERSION) + все SECRET_ENCRYPTION_KEY__v<N>  legacy
#   - REDIS_STASH_ENCRYPTION_KEY, CREDS_STASH_ENCRYPTION_KEY (+ _VERSION) + legacy (если в Secret'е есть)
#   - HKDF_SALT_HEX
#   - AUTH_SECRET_KEY (JWT signing — без него все выданные токены умрут)
#   - INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD / INITIAL_ADMIN_EMAIL
#       (без них после restore некому залогиниться, recovery-скрипт seed'а
#        admin'а сравнивает hash именно с этим паролем).
#   - DOCKER_RSA_PRIVATE_KEY (если есть; нужен для registry token-flow)
#   - dbos-ca-key-pair — отдельный Secret cert-manager'а с приватником CA. Без него
#     cert-manager не сможет ре-выпускать сертификаты сервисам после DR, придётся
#     поднимать новый CA и перевыдавать клиентам — поэтому забираем сразу.
#
# Шифрование (в порядке предпочтения):
#   1. age  — если установлен, requires recipients or passphrase.
#   2. gpg  --symmetric --cipher-algo AES256
#   3. openssl enc -aes-256-gcm -pbkdf2 -salt (последний fallback)
#
# Passphrase берётся:
#   - из env BACKUP_PASSPHRASE, если задано (для cron / scripted-режима);
#   - иначе спрашиваем интерактивно (read -s, два раза).
# Для age можно передать BACKUP_AGE_RECIPIENTS="age1xxx,age1yyy" — тогда
# шифруем под публичные ключи (без passphrase).
#
# Использование:
#   scripts/k8s/backup_master_keys.sh
#   BACKUP_TARGET_DIR=/srv/backup/dbos scripts/k8s/backup_master_keys.sh
#   BACKUP_PASSPHRASE=... scripts/k8s/backup_master_keys.sh
#   BACKUP_AGE_RECIPIENTS="age1xxx" scripts/k8s/backup_master_keys.sh
#
# Безопасность:
#   - target dir: chmod 700.
#   - результирующий файл: chmod 600.
#   - distinct plaintext-копии (распакованный tar и его содержимое) удаляются
#     через `shred -u` (если shred недоступен — `rm -f` + warning).

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
TARGET_DIR="${BACKUP_TARGET_DIR:-/backup/master-keys}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_BASENAME="dbos-master-keys-${TS}.tar.gz"

# ── Утилиты ──────────────────────────────────────────────────────────────────

require_bin() {
    command -v "$1" >/dev/null 2>&1 || { echo "ОШИБКА: нужен $1 в PATH." >&2; exit 1; }
}

require_bin kubectl
require_bin jq
require_bin tar
require_bin sha256sum

# shred — желательно, но не обязательно.
SHRED_BIN="$(command -v shred 2>/dev/null || true)"

secure_delete() {
    local path="$1"
    if [[ ! -e "$path" ]]; then
        return 0
    fi
    if [[ -n "$SHRED_BIN" && -f "$path" ]]; then
        "$SHRED_BIN" -u -- "$path" 2>/dev/null || rm -f -- "$path"
    else
        rm -f -- "$path"
    fi
}

# Достать конкретное поле из Secret'а в plain.
secret_get() {
    local key="$1"
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r --arg k "$key" '.data[$k] // empty' \
        | { local b64; b64=$(cat); [[ -n "$b64" ]] && echo "$b64" | base64 -d || true; }
}

# Список всех имён data-полей в Secret'е.
secret_keys() {
    kubectl -n "$NS" get secret "$SECRET" -o json | jq -r '.data | keys[]'
}

# ── Sanity ───────────────────────────────────────────────────────────────────

if ! kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
    echo "ОШИБКА: Secret $NS/$SECRET не найден. Проверь DBOS_NAMESPACE / DBOS_SECRET_NAME." >&2
    exit 1
fi

mkdir -p "$TARGET_DIR"
chmod 700 "$TARGET_DIR"

# ── Выбор шифровальщика ──────────────────────────────────────────────────────
# Предпочитаем age, потом gpg, потом openssl.
ENCRYPTOR=""
if command -v age >/dev/null 2>&1; then
    ENCRYPTOR="age"
elif command -v gpg >/dev/null 2>&1; then
    ENCRYPTOR="gpg"
elif command -v openssl >/dev/null 2>&1; then
    ENCRYPTOR="openssl"
else
    echo "ОШИБКА: нет ни age, ни gpg, ни openssl. Шифровать нечем." >&2
    exit 1
fi

# Для age с recipients passphrase не нужен.
USE_AGE_RECIPIENTS="false"
if [[ "$ENCRYPTOR" == "age" && -n "${BACKUP_AGE_RECIPIENTS:-}" ]]; then
    USE_AGE_RECIPIENTS="true"
fi

PASSPHRASE=""
PASSPHRASE_SECRET_NAME="${BACKUP_PASSPHRASE_SECRET_NAME:-dbos-backup-passphrase}"
if [[ "$USE_AGE_RECIPIENTS" == "false" ]]; then
    if [[ -n "${BACKUP_PASSPHRASE:-}" ]]; then
        PASSPHRASE="$BACKUP_PASSPHRASE"
    elif kubectl -n "$NS" get secret "$PASSPHRASE_SECRET_NAME" >/dev/null 2>&1; then
        # ad-hoc запуск с оператор-хоста: тянем passphrase из k8s Secret'а
        # `dbos-backup-passphrase` (см. k8s/106-backup-passphrase.yaml.example).
        # CronJob уже пробрасывает её через env BACKUP_PASSPHRASE — этот блок для него no-op.
        PASSPHRASE="$(
            kubectl -n "$NS" get secret "$PASSPHRASE_SECRET_NAME" -o json \
                | jq -r '.data.BACKUP_PASSPHRASE // empty' \
                | { b64=$(cat); [[ -n "$b64" ]] && echo "$b64" | base64 -d || true; }
        )"
        if [[ -z "$PASSPHRASE" ]]; then
            echo "ОШИБКА: Secret ${NS}/${PASSPHRASE_SECRET_NAME} есть, но поле BACKUP_PASSPHRASE пустое." >&2
            exit 1
        fi
        echo "→ passphrase подтянута из Secret ${NS}/${PASSPHRASE_SECRET_NAME}." >&2
    else
        # Интерактивный ввод.
        if [[ ! -t 0 ]]; then
            echo "ОШИБКА: stdin не terminal, и BACKUP_PASSPHRASE не задана." >&2
            echo "  Создай Secret ${NS}/${PASSPHRASE_SECRET_NAME} (см. k8s/106-backup-passphrase.yaml.example)" >&2
            echo "  или передай BACKUP_PASSPHRASE через env." >&2
            exit 1
        fi
        read -r -s -p "Passphrase для backup'а master-ключей: " PASSPHRASE
        echo ""
        read -r -s -p "Повтори passphrase: " PASSPHRASE2
        echo ""
        if [[ "$PASSPHRASE" != "$PASSPHRASE2" ]]; then
            echo "ОШИБКА: passphrase'ы не совпали." >&2
            exit 1
        fi
        if [[ ${#PASSPHRASE} -lt 12 ]]; then
            echo "ОШИБКА: passphrase минимум 12 символов." >&2
            exit 1
        fi
        unset PASSPHRASE2
    fi
fi

# ── Сборка plaintext-набора ──────────────────────────────────────────────────
WORK_DIR="$(mktemp -d -t dbos-mkbk.XXXXXX)"
chmod 700 "$WORK_DIR"

cleanup() {
    # На любом выходе вычищаем plaintext-материал.
    if [[ -d "$WORK_DIR" ]]; then
        find "$WORK_DIR" -type f -print0 2>/dev/null \
            | while IFS= read -r -d '' f; do
                secure_delete "$f"
            done
        rmdir "$WORK_DIR" 2>/dev/null || rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT INT TERM

# Фиксированный набор «обязательных» ключей.
ALWAYS_KEYS=(
    SERVER_ENCRYPTION_KEY
    SERVER_ENCRYPTION_KEY_VERSION
    SECRET_ENCRYPTION_KEY
    SECRET_ENCRYPTION_KEY_VERSION
    HKDF_SALT_HEX
    AUTH_SECRET_KEY
    INITIAL_ADMIN_USERNAME
    INITIAL_ADMIN_PASSWORD
    INITIAL_ADMIN_EMAIL
)

# Опциональные — если есть в Secret'е, тоже забираем.
OPTIONAL_KEYS=(
    REDIS_STASH_ENCRYPTION_KEY
    REDIS_STASH_ENCRYPTION_KEY_VERSION
    CREDS_STASH_ENCRYPTION_KEY
    CREDS_STASH_ENCRYPTION_KEY_VERSION
    DOCKER_RSA_PRIVATE_KEY
)

# Все legacy-версии SERVER/SECRET/REDIS_STASH/CREDS_STASH ENCRYPTION_KEY__v<N>.
LEGACY_KEYS=()
while IFS= read -r k; do
    case "$k" in
        SERVER_ENCRYPTION_KEY__v*|SECRET_ENCRYPTION_KEY__v*|REDIS_STASH_ENCRYPTION_KEY__v*|CREDS_STASH_ENCRYPTION_KEY__v*)
            LEGACY_KEYS+=("$k")
            ;;
    esac
done < <(secret_keys)

KEYS_DIR="$WORK_DIR/keys"
mkdir -p "$KEYS_DIR"
chmod 700 "$KEYS_DIR"

MANIFEST="$WORK_DIR/MANIFEST.txt"
{
    echo "dbos-master-keys backup"
    echo "timestamp_utc: ${TS}"
    echo "namespace:     ${NS}"
    echo "secret:        ${SECRET}"
    echo "host:          $(hostname)"
    echo ""
    echo "files:"
} > "$MANIFEST"

write_key() {
    local key="$1"
    local val
    val="$(secret_get "$key" || true)"
    if [[ -z "$val" ]]; then
        return 1
    fi
    # Используем printf без trailing \n чтобы при restore значение совпало байт в байт.
    printf '%s' "$val" > "$KEYS_DIR/$key"
    chmod 600 "$KEYS_DIR/$key"
    echo "  - $key (${#val} bytes)" >> "$MANIFEST"
    return 0
}

MISSING=()
for k in "${ALWAYS_KEYS[@]}"; do
    if ! write_key "$k"; then
        MISSING+=("$k")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "ОШИБКА: в Secret'е нет обязательных полей: ${MISSING[*]}" >&2
    exit 1
fi

for k in "${OPTIONAL_KEYS[@]}"; do
    write_key "$k" || true
done

for k in "${LEGACY_KEYS[@]}"; do
    write_key "$k" || true
done

# ── CA Secret cert-manager'а ─────────────────────────────────────────────────
# dbos-ca-key-pair — Issuer'у нужен CA-приватник, а это отдельный k8s Secret
# (не поле в dbos-secrets). Кладём целиком YAML-ом в подкаталог ca/, restore
# подкатит обратно `kubectl apply -f`. Если Secret'а нет (свежий кластер,
# CA ещё не выпущен) — пропускаем без ошибки.
CA_SECRET_NAME="${DBOS_CA_SECRET_NAME:-dbos-ca-key-pair}"
CA_DIR="$WORK_DIR/ca"
mkdir -p "$CA_DIR"
chmod 700 "$CA_DIR"

if kubectl -n "$NS" get secret "$CA_SECRET_NAME" >/dev/null 2>&1; then
    CA_YAML="$CA_DIR/${CA_SECRET_NAME}.yaml"
    kubectl -n "$NS" get secret "$CA_SECRET_NAME" -o yaml \
        | sed -E \
            -e '/^  resourceVersion:/d' \
            -e '/^  uid:/d' \
            -e '/^  creationTimestamp:/d' \
            -e '/^  managedFields:/,/^  [a-z]/{/^  [a-z]/!d;}' \
        > "$CA_YAML"
    chmod 600 "$CA_YAML"
    CA_SIZE=$(stat -c '%s' "$CA_YAML" 2>/dev/null || wc -c < "$CA_YAML")
    echo "  - ca/${CA_SECRET_NAME}.yaml (${CA_SIZE} bytes)" >> "$MANIFEST"
else
    echo "  - ca/${CA_SECRET_NAME}.yaml: SKIPPED (Secret не найден в $NS)" >> "$MANIFEST"
fi

# ── Durable keystore Secret'ы ────────────────────────────────────────────────
# server/secret-service в prod держат master-ключи шифрования в отдельных
# Secret'ах (KEYSTORE_BACKEND=k8s), а не в полях dbos-secrets. Именно там живёт
# АКТУАЛЬНЫЙ, ротированный материал (active_version + key_v<N>) — без него
# backup бесполезен после первой ротации. Кладём оба Secret'а целиком YAML'ом в
# keystores/, restore подкатит обратно `kubectl apply -f`. Если Secret'а нет
# (file-бэкенд / ещё не bootstrap'ился) — пропускаем без ошибки.
KEYSTORE_SECRET_NAMES=(dbos-server-encryption-keys dbos-secret-encryption-keys)
KEYSTORES_DIR="$WORK_DIR/keystores"
mkdir -p "$KEYSTORES_DIR"
chmod 700 "$KEYSTORES_DIR"

for ks_name in "${KEYSTORE_SECRET_NAMES[@]}"; do
    if kubectl -n "$NS" get secret "$ks_name" >/dev/null 2>&1; then
        KS_YAML="$KEYSTORES_DIR/${ks_name}.yaml"
        kubectl -n "$NS" get secret "$ks_name" -o yaml \
            | sed -E \
                -e '/^  resourceVersion:/d' \
                -e '/^  uid:/d' \
                -e '/^  creationTimestamp:/d' \
                -e '/^  managedFields:/,/^  [a-z]/{/^  [a-z]/!d;}' \
            > "$KS_YAML"
        chmod 600 "$KS_YAML"
        KS_SIZE=$(stat -c '%s' "$KS_YAML" 2>/dev/null || wc -c < "$KS_YAML")
        echo "  - keystores/${ks_name}.yaml (${KS_SIZE} bytes)" >> "$MANIFEST"
    else
        echo "  - keystores/${ks_name}.yaml: SKIPPED (Secret не найден в $NS)" >> "$MANIFEST"
    fi
done

# ── tar.gz ───────────────────────────────────────────────────────────────────
TAR_PATH="$WORK_DIR/$ARCHIVE_BASENAME"
# В архив кладём MANIFEST.txt + keys/ + ca/ + keystores/ (пустые каталоги tar
# всё равно сохранит, при restore просто будут пустыми).
( cd "$WORK_DIR" && tar -czf "$TAR_PATH" -C "$WORK_DIR" MANIFEST.txt keys ca keystores )
chmod 600 "$TAR_PATH"

TAR_SHA256_PLAIN="$(sha256sum "$TAR_PATH" | awk '{print $1}')"

# ── Шифрование ───────────────────────────────────────────────────────────────
case "$ENCRYPTOR" in
    age)
        FINAL_PATH="$TARGET_DIR/${ARCHIVE_BASENAME}.age"
        if [[ "$USE_AGE_RECIPIENTS" == "true" ]]; then
            # Список через запятую → отдельные -r аргументы.
            IFS=',' read -r -a RECIPS <<< "$BACKUP_AGE_RECIPIENTS"
            AGE_ARGS=()
            for r in "${RECIPS[@]}"; do
                AGE_ARGS+=( -r "$r" )
            done
            age "${AGE_ARGS[@]}" -o "$FINAL_PATH" "$TAR_PATH"
        else
            # Symmetric: passphrase через AGE-окружение или TTY.
            # age читает passphrase с TTY, обходим через expect-like подход:
            # передаём через stdin pty эмулятор не нужен — у age есть -p (passphrase).
            # Однако в неинтерактивном режиме age требует TTY. Workaround:
            # делаем temp file и шифруем через age, отдавая passphrase через
            # /dev/tty не получится; используем env-managed approach:
            # самый портативный путь — запустить age с -p и stdin-pipe только
            # данных, а passphrase — через AGE_PASSPHRASE через `expect`.
            # У нас нет expect зависимости. Поэтому: если passphrase задана но
            # нет recipients, делаем gpg fallback.
            echo "age symmetric из неинтерактивного passphrase ненадёжен — fallback на gpg." >&2
            ENCRYPTOR="gpg"
        fi
        ;;
esac

if [[ "$ENCRYPTOR" == "gpg" ]]; then
    if ! command -v gpg >/dev/null 2>&1; then
        ENCRYPTOR="openssl"
    fi
fi

case "$ENCRYPTOR" in
    gpg)
        FINAL_PATH="$TARGET_DIR/${ARCHIVE_BASENAME}.gpg"
        # --batch + --passphrase-fd 0 чтобы не лезть в /dev/tty.
        printf '%s' "$PASSPHRASE" | gpg \
            --batch --yes --quiet \
            --pinentry-mode loopback \
            --passphrase-fd 0 \
            --symmetric --cipher-algo AES256 \
            --output "$FINAL_PATH" \
            "$TAR_PATH"
        ;;
    openssl)
        FINAL_PATH="$TARGET_DIR/${ARCHIVE_BASENAME}.enc"
        # -pass через env, чтобы passphrase не светилась в /proc/<pid>/cmdline.
        BACKUP_PP_ENV="$PASSPHRASE" openssl enc \
            -aes-256-gcm -pbkdf2 -iter 200000 -salt \
            -in "$TAR_PATH" -out "$FINAL_PATH" \
            -pass env:BACKUP_PP_ENV
        ;;
esac

unset PASSPHRASE
chmod 600 "$FINAL_PATH"

FINAL_SHA256="$(sha256sum "$FINAL_PATH" | awk '{print $1}')"
FINAL_SIZE="$(stat -c '%s' "$FINAL_PATH" 2>/dev/null || wc -c < "$FINAL_PATH")"

# Sidecar с метаданными — для verify при restore. plaintext-sha считается для
# .tar.gz ДО шифрования (его проверяет restore после расшифровки).
META_PATH="${FINAL_PATH}.meta"
{
    echo "archive:        $(basename "$FINAL_PATH")"
    echo "timestamp_utc:  ${TS}"
    echo "namespace:      ${NS}"
    echo "secret:         ${SECRET}"
    echo "encryptor:      ${ENCRYPTOR}"
    echo "size_bytes:     ${FINAL_SIZE}"
    echo "sha256_cipher:  ${FINAL_SHA256}"
    echo "sha256_plain:   ${TAR_SHA256_PLAIN}"
} > "$META_PATH"
chmod 600 "$META_PATH"

# ── Финал ────────────────────────────────────────────────────────────────────
echo ""
echo "✓ Backup master-ключей готов."
echo "  archive:        ${FINAL_PATH}"
echo "  sha256:         ${FINAL_SHA256}"
echo "  size:           ${FINAL_SIZE} bytes"
echo "  encryptor:      ${ENCRYPTOR}"
echo "  meta:           ${META_PATH}"
echo ""
echo "  Перенеси архив в offsite-хранилище (сейф, отдельный носитель)."
echo "  Без него потеря master-ключа = безвозвратная утрата ciphertext'ов."
