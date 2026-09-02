#!/usr/bin/env bash
# Smoke-test: backup → restore master-keys, без потери данных.
#
# НЕ запускается из make. Это локальная ручная проверка, требует доступа к
# kubectl (любой namespace, по умолчанию dbos-mkbk-test — создаётся скриптом).
#
# Что делает:
#   1. Создаёт namespace ${TEST_NS} и Secret ${TEST_SECRET} с фейковыми
#      master-ключами и legacy __v<N>.
#   2. Запускает scripts/k8s/backup_master_keys.sh в --target-dir tmp.
#   3. Стирает Secret.
#   4. Запускает scripts/k8s/restore_master_keys.sh --apply.
#   5. diff'ает оригинальные значения с восстановленными — все должны совпасть.
#   6. Чистит namespace.
#
# Использование:
#   tests/k8s/test_master_keys_roundtrip.sh                 # gpg по умолчанию
#   TEST_ENCRYPTOR=openssl tests/k8s/test_master_keys_roundtrip.sh
#   TEST_NS=dbos-mkbk-foo tests/k8s/test_master_keys_roundtrip.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

TEST_NS="${TEST_NS:-dbos-mkbk-test}"
TEST_SECRET="${TEST_SECRET:-dbos-secrets}"
TEST_ENCRYPTOR="${TEST_ENCRYPTOR:-gpg}"  # gpg | openssl | age

WORK="$(mktemp -d -t dbos-mkbk-test.XXXXXX)"
chmod 700 "$WORK"

PASSPHRASE="test-passphrase-not-secret-1234567890"

cleanup() {
    kubectl delete namespace "$TEST_NS" --wait=false >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

echo "── Setup тестового Secret'а ──"
kubectl create namespace "$TEST_NS" 2>/dev/null || true

# Заведомо известные значения, чтобы потом diff'ать.
declare -A FIXTURE=(
    [SERVER_ENCRYPTION_KEY]="AAAA-current-server-key-v3-base64-fixture"
    [SERVER_ENCRYPTION_KEY_VERSION]="3"
    [SERVER_ENCRYPTION_KEY__v2]="BBBB-legacy-server-key-v2-base64-fixture"
    [SERVER_ENCRYPTION_KEY__v1]="CCCC-legacy-server-key-v1-base64-fixture"
    [SECRET_ENCRYPTION_KEY]="DDDD-current-secret-key-v2-base64-fixture"
    [SECRET_ENCRYPTION_KEY_VERSION]="2"
    [SECRET_ENCRYPTION_KEY__v1]="EEEE-legacy-secret-key-v1-base64-fixture"
    [HKDF_SALT_HEX]="0123456789abcdef0123456789abcdef"
    [AUTH_SECRET_KEY]="FFFF-auth-jwt-signing-key-fixture-1234567890abcdef"
    [DOCKER_RSA_PRIVATE_KEY]=$'-----BEGIN RSA PRIVATE KEY-----\nfake-rsa-line-1\nfake-rsa-line-2\n-----END RSA PRIVATE KEY-----\n'
)

# Собираем kubectl create secret с --from-literal.
LITERAL_ARGS=()
for k in "${!FIXTURE[@]}"; do
    LITERAL_ARGS+=( "--from-literal=${k}=${FIXTURE[$k]}" )
done

kubectl -n "$TEST_NS" create secret generic "$TEST_SECRET" \
    --type=Opaque \
    "${LITERAL_ARGS[@]}"

echo "✓ Secret ${TEST_NS}/${TEST_SECRET} создан с ${#FIXTURE[@]} полями."

# ── Snapshot оригинальных значений ───────────────────────────────────────────
ORIG_DIR="$WORK/orig"
mkdir -p "$ORIG_DIR"
for k in "${!FIXTURE[@]}"; do
    kubectl -n "$TEST_NS" get secret "$TEST_SECRET" -o json \
        | jq -r --arg k "$k" '.data[$k]' \
        | base64 -d > "$ORIG_DIR/$k"
done

# ── Backup ──────────────────────────────────────────────────────────────────
echo ""
echo "── Backup ──"
BACKUP_DIR="$WORK/backup"
mkdir -p "$BACKUP_DIR"

# Mute age (он капризный), форсируем gpg/openssl через PATH-чистку при
# TEST_ENCRYPTOR=openssl. Это эвристика — если age в PATH есть, скрипт
# попробует его первым.
case "$TEST_ENCRYPTOR" in
    openssl)
        # Спрячем age и gpg из PATH временно.
        FAKE_PATH="$WORK/fake-bin"
        mkdir -p "$FAKE_PATH"
        export PATH="$FAKE_PATH:$(echo "$PATH" | tr ':' '\n' | grep -v -E '/(age|gpg)$' | tr '\n' ':')"
        # На самом деле gpg/age лежат в общих bin-каталогах, изоляция полная не
        # выйдет. Альтернатива: тестируем фактически тем encryptor'ом, что найдётся.
        ;;
esac

BACKUP_PASSPHRASE="$PASSPHRASE" \
BACKUP_TARGET_DIR="$BACKUP_DIR" \
DBOS_NAMESPACE="$TEST_NS" \
DBOS_SECRET_NAME="$TEST_SECRET" \
    bash "$REPO_ROOT/scripts/k8s/backup_master_keys.sh"

ARCHIVE="$(find "$BACKUP_DIR" -maxdepth 1 -type f \( -name '*.gpg' -o -name '*.age' -o -name '*.enc' \) | head -n1)"
[[ -n "$ARCHIVE" ]] || { echo "ОШИБКА: backup-скрипт не сделал архив." >&2; exit 1; }
echo "✓ Backup создан: $ARCHIVE"

# ── Wipe Secret ─────────────────────────────────────────────────────────────
echo ""
echo "── Wipe Secret ──"
kubectl -n "$TEST_NS" delete secret "$TEST_SECRET"

# ── Restore --apply ─────────────────────────────────────────────────────────
echo ""
echo "── Restore --apply ──"
BACKUP_PASSPHRASE="$PASSPHRASE" \
DBOS_NAMESPACE="$TEST_NS" \
DBOS_SECRET_NAME="$TEST_SECRET" \
    bash "$REPO_ROOT/scripts/k8s/restore_master_keys.sh" "$ARCHIVE" --apply

# ── Diff ────────────────────────────────────────────────────────────────────
echo ""
echo "── Diff восстановленных полей с оригиналом ──"
FAIL=0
for k in "${!FIXTURE[@]}"; do
    restored="$(kubectl -n "$TEST_NS" get secret "$TEST_SECRET" -o json \
        | jq -r --arg k "$k" '.data[$k] // empty' \
        | base64 -d)"
    if [[ "$restored" != "${FIXTURE[$k]}" ]]; then
        echo "  ✗ $k: mismatch"
        echo "      orig:     ${FIXTURE[$k]}"
        echo "      restored: $restored"
        FAIL=1
    else
        echo "  ✓ $k"
    fi
done

if [[ $FAIL -ne 0 ]]; then
    echo ""
    echo "✗ Roundtrip провален: значения не совпали."
    exit 1
fi

echo ""
echo "✓ Roundtrip успешен. Все ${#FIXTURE[@]} полей восстановлены идентично."
