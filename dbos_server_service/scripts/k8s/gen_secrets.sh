#!/usr/bin/env bash
# Генерация k8s/20-secrets.yaml + 50-ingress.yaml с реальными prod-значениями.
# Идемпотентный: если файлы уже существуют — спрашивает, перезаписывать ли.
#
# Использование:
#   scripts/k8s/gen_secrets.sh                  — спросит домен интерактивно
#   scripts/k8s/gen_secrets.sh dbos.example.com — домен аргументом

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/../../k8s" && pwd)"
SECRETS_OUT="$K8S_DIR/20-secrets.yaml"
INGRESS_OUT="$K8S_DIR/50-ingress.yaml"
INGRESS_TEMPLATE="$K8S_DIR/50-ingress.yaml.template"
ENV_FILE="$K8S_DIR/.env.k8s"

# ── Domain (для Ingress + CN сертификата) ──────────────────────────────────────
DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
    if [[ -f "$ENV_FILE" ]]; then
        # shellcheck source=/dev/null
        . "$ENV_FILE"
        echo "→ Использую сохранённый домен: $INGRESS_HOST"
        DOMAIN="$INGRESS_HOST"
    else
        read -p "Домен (например dbos.example.com): " DOMAIN
        [[ -n "$DOMAIN" ]] || { echo "ОШИБКА: домен пустой." >&2; exit 1; }
    fi
fi

# ── Перезапись ─────────────────────────────────────────────────────────────────
if [[ -f "$SECRETS_OUT" ]] || [[ -f "$INGRESS_OUT" ]]; then
    echo "⚠ Файлы $SECRETS_OUT / $INGRESS_OUT уже существуют."
    echo "  Перезапись СБРОСИТ admin-пароль, аннулирует JWT и заменит TLS-сертификат."
    read -p "  Продолжить? (yes/no): " yn
    [[ "$yn" == "yes" ]] || { echo "Отменено."; exit 0; }
fi

# ── Генератор случайных строк ─────────────────────────────────────────────────
rand() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

AUTH_DB_PASSWORD=$(rand 32)
LOGGING_DB_PASSWORD=$(rand 32)
AUTH_SECRET_KEY=$(rand 64)
LOGGING_SERVICE_API_KEY=$(rand 48)
INITIAL_ADMIN_PASSWORD=$(rand 16)

# ── RSA private key для Docker registry token-flow ────────────────────────────
echo "→ Генерируем RSA private key для Docker registry..."
RSA_PEM=$(openssl genrsa 2048 2>/dev/null)

# ── Self-signed TLS cert для Ingress ──────────────────────────────────────────
echo "→ Генерируем self-signed TLS-сертификат для $DOMAIN..."
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$TMP/tls.key" \
    -out    "$TMP/tls.crt" \
    -days   365 \
    -subj   "/CN=$DOMAIN/O=DBOS Server Manager" \
    -addext "subjectAltName=DNS:$DOMAIN" \
    2>/dev/null

TLS_CRT_B64=$(base64 -w0 < "$TMP/tls.crt")
TLS_KEY_B64=$(base64 -w0 < "$TMP/tls.key")

# ── 20-secrets.yaml ───────────────────────────────────────────────────────────
echo "→ Пишем $SECRETS_OUT..."
{
cat <<EOF
# СГЕНЕРИРОВАНО $(date -u +%Y-%m-%dT%H:%M:%SZ) скриптом scripts/k8s/gen_secrets.sh
# Не коммитить в git (см. .gitignore).

apiVersion: v1
kind: Secret
metadata:
  name: dbos-secrets
  namespace: dbos
type: Opaque
stringData:
  AUTH_DB_USER: auth_user
  AUTH_DB_PASSWORD: ${AUTH_DB_PASSWORD}
  LOGGING_DB_USER: logging_user
  LOGGING_DB_PASSWORD: ${LOGGING_DB_PASSWORD}

  AUTH_SECRET_KEY: ${AUTH_SECRET_KEY}

  DOCKER_RSA_PRIVATE_KEY: |
EOF
    echo "$RSA_PEM" | sed 's/^/    /'
cat <<EOF

  INITIAL_ADMIN_USERNAME: admin
  INITIAL_ADMIN_PASSWORD: ${INITIAL_ADMIN_PASSWORD}
  INITIAL_ADMIN_EMAIL: admin@${DOMAIN}

  LOGGING_SERVICE_API_KEY: ${LOGGING_SERVICE_API_KEY}

---
# TLS-сертификат для Traefik (Ingress host: ${DOMAIN})
apiVersion: v1
kind: Secret
metadata:
  name: dbos-tls
  namespace: dbos
type: kubernetes.io/tls
data:
  tls.crt: ${TLS_CRT_B64}
  tls.key: ${TLS_KEY_B64}
EOF
} > "$SECRETS_OUT"

chmod 600 "$SECRETS_OUT"

# ── 50-ingress.yaml ───────────────────────────────────────────────────────────
echo "→ Генерируем $INGRESS_OUT из шаблона..."
sed "s|__INGRESS_HOST__|${DOMAIN}|g" "$INGRESS_TEMPLATE" > "$INGRESS_OUT"

# ── .env.k8s — сохраним домен для повторных запусков ──────────────────────────
echo "INGRESS_HOST=${DOMAIN}" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo ""
echo "✓ Готово."
echo ""
echo "  Сгенерированы:"
echo "    $SECRETS_OUT       (Secret dbos-secrets + dbos-tls, chmod 600)"
echo "    $INGRESS_OUT       (Ingress + Middleware с host=$DOMAIN)"
echo "    $ENV_FILE          (домен для повторных запусков)"
echo ""
echo "  ⚠ СОХРАНИ admin-пароль СЕЙЧАС:"
echo "    admin / ${INITIAL_ADMIN_PASSWORD}"
echo ""
echo "  ⚠ Self-signed cert валиден 365 дней. Перевыпуск: ${0} ${DOMAIN}"
echo ""
echo "  Для доступа с локальной машины пропиши в /etc/hosts:"
echo "    <VM_IP>  ${DOMAIN}"
