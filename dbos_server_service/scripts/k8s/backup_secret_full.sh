#!/usr/bin/env bash
# Полный snapshot k8s Secret'а dbos-secrets (не только master-ключи).
#
# Зачем отдельный скрипт от backup_master_keys.sh:
#   backup_master_keys.sh бэкапит только криптографические master-ключи
#   (SERVER_/SECRET_/HKDF_/AUTH_/INITIAL_ADMIN_*). А в Secret'е dbos-secrets
#   лежат ещё:
#     - DB-пароли (POSTGRES_PASSWORD_* для всех 5 БД),
#     - S2S-ключи между сервисами (AUTH_S2S_*, SERVER_S2S_*, LOGING_S2S_*, ...),
#     - WORKER_BOT_TOKEN, REDIS_PASSWORD, REDIS_STASH_PASSWORD,
#     - DOCKER_RSA_PRIVATE_KEY, TLS-материалы.
#   При DR-сценарии "VM умерла, Secret потерян, новое железо без перегенерации
#   ключей" — нужен именно полный snapshot, иначе сервисы не стартуют
#   (нет DB-паролей), audit-канал мёртв (нет S2S), TLS не загрузится.
#
# Шифрование (в порядке предпочтения): age → gpg → openssl-aes-256-gcm.
# Без passphrase сырой YAML записывается на диск с warn-print (cron-friendly,
# но небезопасно). Для production всегда задавайте BACKUP_PASSPHRASE.
#
# Использование:
#   scripts/k8s/backup_secret_full.sh
#   BACKUP_PASSPHRASE=... scripts/k8s/backup_secret_full.sh
#   BACKUP_AGE_RECIPIENTS="age1xxx" scripts/k8s/backup_secret_full.sh
#   scripts/k8s/backup_secret_full.sh --status
#   scripts/k8s/backup_secret_full.sh --restore /var/backups/dbos/secret-full-<TS>.yaml.gpg
#
# Флаги:
#   --status                показать список локальных backup'ов.
#   --restore <file>        восстановить Secret из файла (kubectl apply -f),
#                           с подтверждением (overwrite running Secret =
#                           потенциальный attack vector).
#   --namespace NS          переопределить namespace (default dbos).
#   --secret NAME           переопределить имя Secret'а.
#   --target-dir DIR        куда писать (default /var/backups/dbos).
#   --no-encryption         явно разрешить unencrypted YAML (без него скрипт
#                           всё равно сделает unencrypted, но с warn-prompt).
#   --yes                   не спрашивать подтверждение в --restore (для cron).
#
# Безопасность:
#   - target dir: chmod 700.
#   - результирующий файл: chmod 600.
#   - plaintext YAML удаляется через shred (если есть) при выходе.

set -euo pipefail

# ── Цветной echo ─────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_RED=$'\033[31m'
    C_GRN=$'\033[32m'
    C_YEL=$'\033[33m'
    C_BLU=$'\033[34m'
    C_RST=$'\033[0m'
else
    C_RED=""; C_GRN=""; C_YEL=""; C_BLU=""; C_RST=""
fi

log_info()  { echo "${C_BLU}→${C_RST} $*"; }
log_ok()    { echo "${C_GRN}✓${C_RST} $*"; }
log_warn()  { echo "${C_YEL}!${C_RST} $*" >&2; }
log_err()   { echo "${C_RED}ОШИБКА:${C_RST} $*" >&2; }

# ── Параметры ────────────────────────────────────────────────────────────────
NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
TARGET_DIR="${BACKUP_TARGET_DIR:-/var/backups/dbos}"

MODE="backup"
RESTORE_FILE=""
ALLOW_NO_ENC="false"
ASSUME_YES="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --status) MODE="status"; shift ;;
        --restore) MODE="restore"; RESTORE_FILE="${2:-}"; shift 2 ;;
        --namespace) NS="${2:-}"; shift 2 ;;
        --secret) SECRET="${2:-}"; shift 2 ;;
        --target-dir) TARGET_DIR="${2:-}"; shift 2 ;;
        --no-encryption) ALLOW_NO_ENC="true"; shift ;;
        --yes|-y) ASSUME_YES="true"; shift ;;
        -h|--help)
            sed -n '1,50p' "$0" >&2
            exit 0
            ;;
        *)
            log_err "неизвестный флаг $1"
            exit 1
            ;;
    esac
done

# ── Утилиты ──────────────────────────────────────────────────────────────────
require_bin() {
    command -v "$1" >/dev/null 2>&1 || { log_err "нужен $1 в PATH."; exit 1; }
}

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

# ── Режим --status ───────────────────────────────────────────────────────────
if [[ "$MODE" == "status" ]]; then
    if [[ ! -d "$TARGET_DIR" ]]; then
        log_warn "$TARGET_DIR не существует — backup'ов нет."
        exit 0
    fi
    log_info "Локальные backup'ы в $TARGET_DIR:"
    echo ""
    shopt -s nullglob
    found="false"
    for f in "$TARGET_DIR"/secret-full-*.yaml "$TARGET_DIR"/secret-full-*.yaml.gpg \
             "$TARGET_DIR"/secret-full-*.yaml.age "$TARGET_DIR"/secret-full-*.yaml.enc; do
        found="true"
        size="$(stat -c '%s' "$f" 2>/dev/null || wc -c < "$f")"
        mtime="$(stat -c '%y' "$f" 2>/dev/null | cut -d. -f1)"
        age_seconds=$(( $(date +%s) - $(stat -c '%Y' "$f" 2>/dev/null || echo 0) ))
        age_days=$(( age_seconds / 86400 ))
        case "$f" in
            *.gpg) enc="gpg" ;;
            *.age) enc="age" ;;
            *.enc) enc="openssl" ;;
            *.yaml) enc="${C_RED}plaintext${C_RST}" ;;
            *) enc="unknown" ;;
        esac
        printf "  %s\n    size=%s bytes  mtime=%s  age=%dd  enc=%s\n" \
            "$(basename "$f")" "$size" "$mtime" "$age_days" "$enc"
    done
    shopt -u nullglob
    if [[ "$found" == "false" ]]; then
        log_warn "ни одного secret-full-*.* не найдено."
    fi
    exit 0
fi

# ── Режим --restore ──────────────────────────────────────────────────────────
if [[ "$MODE" == "restore" ]]; then
    [[ -n "$RESTORE_FILE" ]] || { log_err "--restore требует путь к файлу."; exit 1; }
    [[ -f "$RESTORE_FILE" ]] || { log_err "$RESTORE_FILE не найден."; exit 1; }

    require_bin kubectl

    # Определяем тип файла.
    case "$RESTORE_FILE" in
        *.gpg) RESTORE_ENC="gpg" ;;
        *.age) RESTORE_ENC="age" ;;
        *.enc) RESTORE_ENC="openssl" ;;
        *.yaml) RESTORE_ENC="none" ;;
        *)
            log_err "не удалось определить тип $RESTORE_FILE (ожидалось .yaml / .yaml.gpg / .yaml.age / .yaml.enc)."
            exit 1
            ;;
    esac

    WORK_DIR="$(mktemp -d -t dbos-secfullre.XXXXXX)"
    chmod 700 "$WORK_DIR"
    cleanup() {
        if [[ -d "$WORK_DIR" ]]; then
            find "$WORK_DIR" -type f -print0 2>/dev/null \
                | while IFS= read -r -d '' f; do secure_delete "$f"; done
            rmdir "$WORK_DIR" 2>/dev/null || rm -rf "$WORK_DIR"
        fi
    }
    trap cleanup EXIT INT TERM

    PLAIN_YAML="$WORK_DIR/secret.yaml"

    if [[ "$RESTORE_ENC" == "none" ]]; then
        cp "$RESTORE_FILE" "$PLAIN_YAML"
        log_warn "восстанавливаем из НЕшифрованного YAML — убедись, что источник доверенный."
    else
        require_bin "$RESTORE_ENC"
        # Passphrase.
        PASSPHRASE=""
        if [[ -n "${BACKUP_PASSPHRASE:-}" ]]; then
            PASSPHRASE="$BACKUP_PASSPHRASE"
        elif [[ "$RESTORE_ENC" != "age" || -z "${BACKUP_AGE_IDENTITY_FILE:-}" ]]; then
            if [[ ! -t 0 ]]; then
                log_err "нужен интерактивный passphrase, но stdin не tty."
                exit 1
            fi
            read -r -s -p "Passphrase для архива: " PASSPHRASE
            echo ""
        fi

        case "$RESTORE_ENC" in
            gpg)
                printf '%s' "$PASSPHRASE" | gpg \
                    --batch --yes --quiet \
                    --pinentry-mode loopback \
                    --passphrase-fd 0 \
                    --decrypt \
                    --output "$PLAIN_YAML" \
                    "$RESTORE_FILE"
                ;;
            age)
                if [[ -n "${BACKUP_AGE_IDENTITY_FILE:-}" ]]; then
                    age -d -i "$BACKUP_AGE_IDENTITY_FILE" -o "$PLAIN_YAML" "$RESTORE_FILE"
                else
                    age -d -o "$PLAIN_YAML" "$RESTORE_FILE"
                fi
                ;;
            openssl)
                BACKUP_PP_ENV="$PASSPHRASE" openssl enc -d \
                    -aes-256-gcm -pbkdf2 -iter 200000 -salt \
                    -in "$RESTORE_FILE" -out "$PLAIN_YAML" \
                    -pass env:BACKUP_PP_ENV
                ;;
        esac
        unset PASSPHRASE
    fi
    chmod 600 "$PLAIN_YAML"

    # Sanity: ожидаем kind: Secret и matching name.
    if ! grep -q '^kind: Secret' "$PLAIN_YAML"; then
        log_err "расшифрованный YAML — не Kubernetes Secret. Stop."
        exit 1
    fi
    if ! grep -qE "^  name: ${SECRET}\$" "$PLAIN_YAML"; then
        log_warn "имя Secret'а в YAML не совпадает с $SECRET — будет применено как есть из файла."
    fi

    # Existing Secret?
    EXISTING="false"
    if kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
        EXISTING="true"
    fi

    echo ""
    log_info "Готов восстановить Secret ${NS}/${SECRET} из $RESTORE_FILE"
    if [[ "$EXISTING" == "true" ]]; then
        log_warn "Secret ${NS}/${SECRET} УЖЕ существует. Restore перезапишет его."
        log_warn "  В running-кластере это меняет DB-пароли, S2S, ключи шифрования у работающих pod'ов."
        log_warn "  Pod'ы НЕ перечитают Secret автоматически — нужен rollout restart."
    fi

    if [[ "$ASSUME_YES" != "true" ]]; then
        if [[ ! -t 0 ]]; then
            log_err "нужно подтверждение, но stdin не tty. Передай --yes для cron."
            exit 1
        fi
        read -r -p "Применить kubectl apply -f? (введи 'yes' для подтверждения): " CONFIRM
        if [[ "$CONFIRM" != "yes" ]]; then
            log_warn "отменено пользователем."
            exit 1
        fi
    fi

    kubectl -n "$NS" apply -f "$PLAIN_YAML"
    log_ok "Secret ${NS}/${SECRET} восстановлен."
    echo ""
    log_info "Перезапусти сервисы, чтобы они перечитали Secret:"
    echo "    kubectl -n ${NS} rollout restart deploy --all"
    exit 0
fi

# ── Режим backup (default) ───────────────────────────────────────────────────
require_bin kubectl

if ! kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
    log_err "Secret $NS/$SECRET не найден. Проверь DBOS_NAMESPACE / DBOS_SECRET_NAME."
    exit 1
fi

mkdir -p "$TARGET_DIR"
chmod 700 "$TARGET_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
BASENAME="secret-full-${TS}.yaml"

WORK_DIR="$(mktemp -d -t dbos-secfull.XXXXXX)"
chmod 700 "$WORK_DIR"
cleanup() {
    if [[ -d "$WORK_DIR" ]]; then
        find "$WORK_DIR" -type f -print0 2>/dev/null \
            | while IFS= read -r -d '' f; do secure_delete "$f"; done
        rmdir "$WORK_DIR" 2>/dev/null || rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT INT TERM

PLAIN_YAML="$WORK_DIR/$BASENAME"
log_info "Дамплю Secret ${NS}/${SECRET} → $BASENAME"
# Чистим runtime-метаданные (resourceVersion, uid, creationTimestamp), чтобы
# YAML был «портативным» для apply на новый кластер.
kubectl -n "$NS" get secret "$SECRET" -o yaml \
    | sed -E \
        -e '/^  resourceVersion:/d' \
        -e '/^  uid:/d' \
        -e '/^  creationTimestamp:/d' \
        -e '/^  managedFields:/,/^  [a-z]/{/^  [a-z]/!d;}' \
    > "$PLAIN_YAML"
chmod 600 "$PLAIN_YAML"

# Чекни что что-то прочитали.
if [[ ! -s "$PLAIN_YAML" ]]; then
    log_err "kubectl вернул пустой YAML."
    exit 1
fi

# ── Шифрование ───────────────────────────────────────────────────────────────
# Если passphrase не задана через env, но в кластере есть Secret
# `dbos-backup-passphrase` (см. k8s/106-backup-passphrase.yaml.example),
# подтягиваем passphrase из него. Это нужно для ad-hoc запусков с
# оператор-хоста (CronJob уже пробрасывает BACKUP_PASSPHRASE через
# valueFrom.secretKeyRef, для него этот блок no-op).
PASSPHRASE="${BACKUP_PASSPHRASE:-}"
PASSPHRASE_SECRET_NAME="${BACKUP_PASSPHRASE_SECRET_NAME:-dbos-backup-passphrase}"
if [[ -z "$PASSPHRASE" ]]; then
    if kubectl -n "$NS" get secret "$PASSPHRASE_SECRET_NAME" >/dev/null 2>&1; then
        # data.BACKUP_PASSPHRASE — base64 в Secret'е; jq -r выдаёт пустую
        # строку, если поля нет (а не падает).
        PASSPHRASE="$(
            kubectl -n "$NS" get secret "$PASSPHRASE_SECRET_NAME" -o json \
                | jq -r '.data.BACKUP_PASSPHRASE // empty' \
                | { b64=$(cat); [[ -n "$b64" ]] && echo "$b64" | base64 -d || true; }
        )"
        if [[ -n "$PASSPHRASE" ]]; then
            log_info "Passphrase подтянута из Secret ${NS}/${PASSPHRASE_SECRET_NAME}."
        else
            log_warn "Secret ${NS}/${PASSPHRASE_SECRET_NAME} есть, но поле BACKUP_PASSPHRASE пустое."
        fi
    fi
fi

ENCRYPTOR=""
if command -v age >/dev/null 2>&1 && [[ -n "${BACKUP_AGE_RECIPIENTS:-}" ]]; then
    ENCRYPTOR="age"
elif command -v gpg >/dev/null 2>&1; then
    ENCRYPTOR="gpg"
elif command -v openssl >/dev/null 2>&1; then
    ENCRYPTOR="openssl"
fi
USE_AGE_RECIPIENTS="false"
if [[ "$ENCRYPTOR" == "age" && -n "${BACKUP_AGE_RECIPIENTS:-}" ]]; then
    USE_AGE_RECIPIENTS="true"
fi

if [[ -z "$ENCRYPTOR" || ( -z "$PASSPHRASE" && "$USE_AGE_RECIPIENTS" == "false" ) ]]; then
    # Нет ни шифровальщика, ни passphrase → unencrypted.
    log_warn "Backup будет НЕшифрованный (нет BACKUP_PASSPHRASE / BACKUP_AGE_RECIPIENTS)."
    log_warn "  Сырой YAML содержит ВСЕ секреты в plain. Защити backup-storage сам."
    if [[ "$ALLOW_NO_ENC" != "true" && -t 0 ]]; then
        read -r -p "Продолжить без шифрования? (yes/no): " ANSWER
        if [[ "$ANSWER" != "yes" ]]; then
            log_warn "отменено."
            exit 1
        fi
    fi
    FINAL_PATH="$TARGET_DIR/$BASENAME"
    cp "$PLAIN_YAML" "$FINAL_PATH"
    chmod 600 "$FINAL_PATH"
    log_ok "Backup записан: $FINAL_PATH (${C_RED}plaintext${C_RST})"
else
    case "$ENCRYPTOR" in
        age)
            FINAL_PATH="$TARGET_DIR/${BASENAME}.age"
            IFS=',' read -r -a RECIPS <<< "$BACKUP_AGE_RECIPIENTS"
            AGE_ARGS=()
            for r in "${RECIPS[@]}"; do
                AGE_ARGS+=( -r "$r" )
            done
            age "${AGE_ARGS[@]}" -o "$FINAL_PATH" "$PLAIN_YAML"
            ;;
        gpg)
            FINAL_PATH="$TARGET_DIR/${BASENAME}.gpg"
            printf '%s' "$PASSPHRASE" | gpg \
                --batch --yes --quiet \
                --pinentry-mode loopback \
                --passphrase-fd 0 \
                --symmetric --cipher-algo AES256 \
                --output "$FINAL_PATH" \
                "$PLAIN_YAML"
            ;;
        openssl)
            FINAL_PATH="$TARGET_DIR/${BASENAME}.enc"
            BACKUP_PP_ENV="$PASSPHRASE" openssl enc \
                -aes-256-gcm -pbkdf2 -iter 200000 -salt \
                -in "$PLAIN_YAML" -out "$FINAL_PATH" \
                -pass env:BACKUP_PP_ENV
            ;;
    esac
    unset PASSPHRASE
    chmod 600 "$FINAL_PATH"
    log_ok "Backup зашифрован: $FINAL_PATH (encryptor=$ENCRYPTOR)"
fi

FINAL_SIZE="$(stat -c '%s' "$FINAL_PATH" 2>/dev/null || wc -c < "$FINAL_PATH")"
FINAL_SHA="$(sha256sum "$FINAL_PATH" | awk '{print $1}')"

echo ""
echo "  path:    $FINAL_PATH"
echo "  size:    $FINAL_SIZE bytes"
echo "  sha256:  $FINAL_SHA"
echo ""

if [[ -n "${OFFSITE_HOST:-}" ]]; then
    log_info "OFFSITE_HOST=$OFFSITE_HOST — копию в offsite этот скрипт не делает."
    log_info "  Воспользуйся отдельным backup-offsite.sh / rsync ${FINAL_PATH} ${OFFSITE_HOST}:/srv/backups/dbos/"
fi

echo ""
log_warn "Перенеси файл в offsite-хранилище (сейф / отдельный носитель / S3 с lifecycle)."
log_warn "  Этот snapshot содержит DB-пароли, S2S-ключи, master-ключи, RSA-cert — всё."
