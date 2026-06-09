#!/usr/bin/env bash
# Восстановление master-ключей DBOS из шифрованного архива, созданного
# backup_master_keys.sh.
#
# Flow (по умолчанию dry-run):
#   1. Расшифровать архив в tmp (mode 600, mktemp).
#   2. Сверить sha256 с .meta-сайдкаром (если есть) и/или с --expected-sha256.
#   3. Распаковать tar и показать MANIFEST.
#   4. Сгенерировать kubectl-патч (stringData) и:
#        - без --apply: только показать `kubectl apply --dry-run=server` preview
#          и команду, которой можно вкатить руками;
#        - с --apply:   merge-патч прямо в Secret.
#   5. Удалить plaintext через shred.
#
# Использование:
#   scripts/k8s/restore_master_keys.sh path/to/dbos-master-keys-*.{age,gpg,enc}
#   scripts/k8s/restore_master_keys.sh archive.gpg --apply
#   scripts/k8s/restore_master_keys.sh archive.gpg --prompt
#   BACKUP_PASSPHRASE=... scripts/k8s/restore_master_keys.sh archive.gpg
#
# Флаги:
#   --apply                 действительно записать в Secret (merge).
#   --prompt                принудительно спросить passphrase интерактивно.
#   --expected-sha256 HEX   сверить sha256 расшифрованного tar.gz с HEX.
#   --namespace NS          переопределить namespace (default из env / dbos).
#   --secret NAME           переопределить имя Secret'а.

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"

ARCHIVE=""
APPLY="false"
PROMPT="false"
EXPECTED_SHA=""

usage() {
    sed -n '1,30p' "$0" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply) APPLY="true"; shift ;;
        --prompt) PROMPT="true"; shift ;;
        --expected-sha256) EXPECTED_SHA="${2:-}"; shift 2 ;;
        --namespace) NS="${2:-}"; shift 2 ;;
        --secret) SECRET="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        -*)
            echo "ОШИБКА: неизвестный флаг $1" >&2
            usage
            ;;
        *)
            if [[ -z "$ARCHIVE" ]]; then
                ARCHIVE="$1"
            else
                echo "ОШИБКА: несколько positional-аргументов." >&2
                usage
            fi
            shift
            ;;
    esac
done

[[ -n "$ARCHIVE" ]] || { echo "ОШИБКА: путь к архиву не задан." >&2; usage; }
[[ -f "$ARCHIVE" ]] || { echo "ОШИБКА: $ARCHIVE не найден." >&2; exit 1; }

require_bin() {
    command -v "$1" >/dev/null 2>&1 || { echo "ОШИБКА: нужен $1 в PATH." >&2; exit 1; }
}

require_bin kubectl
require_bin jq
require_bin tar
require_bin sha256sum

SHRED_BIN="$(command -v shred 2>/dev/null || true)"
secure_delete() {
    local path="$1"
    [[ -e "$path" ]] || return 0
    if [[ -n "$SHRED_BIN" && -f "$path" ]]; then
        "$SHRED_BIN" -u -- "$path" 2>/dev/null || rm -f -- "$path"
    else
        rm -f -- "$path"
    fi
}

# ── Определяем тип шифрования по расширению ──────────────────────────────────
case "$ARCHIVE" in
    *.age) ENCRYPTOR="age" ;;
    *.gpg) ENCRYPTOR="gpg" ;;
    *.enc) ENCRYPTOR="openssl" ;;
    *)
        echo "ОШИБКА: не удалось определить шифровальщик по расширению $ARCHIVE." >&2
        echo "       Ожидалось .age / .gpg / .enc." >&2
        exit 1
        ;;
esac

require_bin "$ENCRYPTOR"

# ── Sidecar .meta — sha256 проверка ──────────────────────────────────────────
META_PATH="${ARCHIVE}.meta"
META_PLAIN_SHA=""
if [[ -f "$META_PATH" ]]; then
    META_PLAIN_SHA="$(grep -E '^sha256_plain:' "$META_PATH" | awk '{print $2}' || true)"
    META_CIPHER_SHA="$(grep -E '^sha256_cipher:' "$META_PATH" | awk '{print $2}' || true)"
    if [[ -n "$META_CIPHER_SHA" ]]; then
        ACTUAL_CIPHER_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
        if [[ "$META_CIPHER_SHA" != "$ACTUAL_CIPHER_SHA" ]]; then
            echo "ОШИБКА: sha256 архива не сходится с .meta." >&2
            echo "  expected: $META_CIPHER_SHA" >&2
            echo "  actual:   $ACTUAL_CIPHER_SHA" >&2
            exit 1
        fi
        echo "✓ sha256 архива совпал с .meta."
    fi
fi

# ── Passphrase ───────────────────────────────────────────────────────────────
PASSPHRASE=""
if [[ "$ENCRYPTOR" != "age" || -z "${BACKUP_AGE_IDENTITY_FILE:-}" ]]; then
    if [[ "$PROMPT" == "true" || -z "${BACKUP_PASSPHRASE:-}" ]]; then
        if [[ ! -t 0 ]]; then
            echo "ОШИБКА: нужен интерактивный passphrase, но stdin не tty." >&2
            exit 1
        fi
        read -r -s -p "Passphrase для архива: " PASSPHRASE
        echo ""
    else
        PASSPHRASE="$BACKUP_PASSPHRASE"
    fi
fi

# ── Работаем в tmpfs/tmpdir с chmod 700 ──────────────────────────────────────
WORK_DIR="$(mktemp -d -t dbos-mkre.XXXXXX)"
chmod 700 "$WORK_DIR"

cleanup() {
    if [[ -d "$WORK_DIR" ]]; then
        find "$WORK_DIR" -type f -print0 2>/dev/null \
            | while IFS= read -r -d '' f; do
                secure_delete "$f"
            done
        rmdir "$WORK_DIR" 2>/dev/null || rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT INT TERM

TAR_PATH="$WORK_DIR/restored.tar.gz"

# ── Расшифровка ──────────────────────────────────────────────────────────────
case "$ENCRYPTOR" in
    age)
        if [[ -n "${BACKUP_AGE_IDENTITY_FILE:-}" ]]; then
            age -d -i "$BACKUP_AGE_IDENTITY_FILE" -o "$TAR_PATH" "$ARCHIVE"
        else
            # symmetric age — интерактивный ввод passphrase в TTY.
            age -d -o "$TAR_PATH" "$ARCHIVE"
        fi
        ;;
    gpg)
        printf '%s' "$PASSPHRASE" | gpg \
            --batch --yes --quiet \
            --pinentry-mode loopback \
            --passphrase-fd 0 \
            --decrypt \
            --output "$TAR_PATH" \
            "$ARCHIVE"
        ;;
    openssl)
        BACKUP_PP_ENV="$PASSPHRASE" openssl enc -d \
            -aes-256-gcm -pbkdf2 -iter 200000 -salt \
            -in "$ARCHIVE" -out "$TAR_PATH" \
            -pass env:BACKUP_PP_ENV
        ;;
esac
unset PASSPHRASE

chmod 600 "$TAR_PATH"

# ── sha256 расшифрованного tar.gz ────────────────────────────────────────────
ACTUAL_PLAIN_SHA="$(sha256sum "$TAR_PATH" | awk '{print $1}')"
echo "  sha256_plain(actual):   ${ACTUAL_PLAIN_SHA}"

if [[ -n "$META_PLAIN_SHA" ]]; then
    if [[ "$META_PLAIN_SHA" != "$ACTUAL_PLAIN_SHA" ]]; then
        echo "ОШИБКА: sha256 расшифрованного архива не совпал с .meta." >&2
        echo "  expected: $META_PLAIN_SHA" >&2
        echo "  actual:   $ACTUAL_PLAIN_SHA" >&2
        exit 1
    fi
    echo "✓ sha256 plaintext совпал с .meta."
fi

if [[ -n "$EXPECTED_SHA" ]]; then
    if [[ "$EXPECTED_SHA" != "$ACTUAL_PLAIN_SHA" ]]; then
        echo "ОШИБКА: sha256 не совпал с --expected-sha256." >&2
        echo "  expected: $EXPECTED_SHA" >&2
        echo "  actual:   $ACTUAL_PLAIN_SHA" >&2
        exit 1
    fi
    echo "✓ sha256 совпал с --expected-sha256."
fi

# ── Распаковка ───────────────────────────────────────────────────────────────
UNPACK_DIR="$WORK_DIR/unpack"
mkdir -p "$UNPACK_DIR"
chmod 700 "$UNPACK_DIR"
tar -xzf "$TAR_PATH" -C "$UNPACK_DIR"

KEYS_DIR="$UNPACK_DIR/keys"
if [[ ! -d "$KEYS_DIR" ]]; then
    echo "ОШИБКА: в архиве нет каталога keys/. Битый backup?" >&2
    exit 1
fi

echo ""
echo "── MANIFEST ──"
if [[ -f "$UNPACK_DIR/MANIFEST.txt" ]]; then
    cat "$UNPACK_DIR/MANIFEST.txt"
else
    echo "(нет MANIFEST.txt в архиве)"
fi
echo ""

# ── Собираем stringData JSON для patch'а ─────────────────────────────────────
STRING_DATA_JSON="$WORK_DIR/patch.json"

# jq собирает {"stringData": {key: file_content, ...}} построчно.
{
    echo '{"stringData":{'
    first="true"
    for f in "$KEYS_DIR"/*; do
        [[ -f "$f" ]] || continue
        key="$(basename "$f")"
        # JSON-escape значения через jq -Rs.
        val_json="$(jq -Rs '.' < "$f")"
        if [[ "$first" == "true" ]]; then
            first="false"
        else
            echo ","
        fi
        printf '  %s: %s' "$(printf '%s' "$key" | jq -Rs '.')" "$val_json"
    done
    echo ""
    echo "}}"
} > "$STRING_DATA_JSON"

# Валидируем JSON.
if ! jq -e . "$STRING_DATA_JSON" >/dev/null; then
    echo "ОШИБКА: собранный patch.json — не валидный JSON." >&2
    exit 1
fi

KEYS_COUNT="$(jq -r '.stringData | length' "$STRING_DATA_JSON")"
echo "→ Будет восстановлено ${KEYS_COUNT} полей в Secret ${NS}/${SECRET}."
echo ""

if [[ "$APPLY" == "true" ]]; then
    if ! kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
        echo "→ Secret ${NS}/${SECRET} ещё не существует — создаю пустой."
        kubectl -n "$NS" create secret generic "$SECRET" --type=Opaque
    fi
    echo "→ Применяю merge-патч..."
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' --patch-file "$STRING_DATA_JSON"
    echo ""
    echo "✓ Master-ключи восстановлены в Secret ${NS}/${SECRET}."

    # ── CA Secret cert-manager'а ─────────────────────────────────────────────
    # Если в архиве есть ca/<name>.yaml — отдельным kubectl apply -f. Полная
    # замена Secret'а (не patch) корректна: CA-материал монолитный, частичный
    # merge с уже существующим CA даст плохо предсказуемый результат.
    CA_DIR_UNPACK="$UNPACK_DIR/ca"
    if [[ -d "$CA_DIR_UNPACK" ]]; then
        for ca_yaml in "$CA_DIR_UNPACK"/*.yaml; do
            [[ -f "$ca_yaml" ]] || continue
            ca_name="$(basename "$ca_yaml" .yaml)"
            echo "→ Восстанавливаю CA Secret ${NS}/${ca_name} (kubectl apply -f)..."
            kubectl -n "$NS" apply -f "$ca_yaml"
            echo "✓ CA Secret ${NS}/${ca_name} применён."
        done
    fi
    echo ""
    echo "  Перезапусти сервисы, чтобы они перечитали Secret:"
    echo "    kubectl -n ${NS} rollout restart deploy/server-service deploy/server-worker"
    echo "    kubectl -n ${NS} rollout restart deploy/secret-service"
    echo "    kubectl -n ${NS} rollout restart deploy/auth-service"
else
    echo "── DRY-RUN preview (server-side) ──"
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' \
        --patch-file "$STRING_DATA_JSON" \
        --dry-run=server -o yaml \
        | sed 's/^/  /'
    CA_DIR_UNPACK="$UNPACK_DIR/ca"
    if [[ -d "$CA_DIR_UNPACK" ]]; then
        for ca_yaml in "$CA_DIR_UNPACK"/*.yaml; do
            [[ -f "$ca_yaml" ]] || continue
            ca_name="$(basename "$ca_yaml" .yaml)"
            echo ""
            echo "── CA Secret в архиве: ${ca_name} (будет применён через kubectl apply -f) ──"
        done
    fi
    echo ""
    echo "── Команда для реального применения ──"
    echo "  scripts/k8s/restore_master_keys.sh \"$ARCHIVE\" --apply"
    echo ""
    echo "  Либо вручную:"
    echo "    kubectl -n ${NS} patch secret ${SECRET} --type=merge --patch-file <path>"
    echo ""
    echo "  Plaintext-патч лежит в ${STRING_DATA_JSON} (chmod 600 в tmpdir,"
    echo "  удалится при выходе скрипта). Если хочешь сохранить — скопируй сейчас:"
    echo "    cp ${STRING_DATA_JSON} /secure/place && chmod 600 /secure/place"
fi
