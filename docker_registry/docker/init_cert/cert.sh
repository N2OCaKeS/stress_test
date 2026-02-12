#!/usr/bin/env sh
set -euo pipefail

: "${REGISTRY_HOST:?REGISTRY_HOST is required}"
CA_DAYS="${CA_DAYS:-3650}"
CERT_DAYS="${CERT_DAYS:-825}"

umask 077
mkdir -p /tls

# --- 1) CA (создаём один раз) ---
if [ ! -s /tls/ca.key ] || [ ! -s /tls/ca.crt ]; then
  echo "[tls-init] Generating CA..."
  openssl genrsa -out /tls/ca.key 4096
  openssl req -x509 -new -nodes -key /tls/ca.key -sha256 -days "${CA_DAYS}" \
    -subj "/CN=AlltaDockerRegistryCA" -out /tls/ca.crt
else
  echo "[tls-init] CA exists, skip."
fi

# --- 2) server key/cert (пересоздаём каждый запуск) ---
echo "[tls-init] Re-generating server key/cert for ${REGISTRY_HOST}..."
rm -f /tls/tls.key /tls/tls.crt /tls/registry.csr /tls/registry.ext

openssl genrsa -out /tls/tls.key 4096
openssl req -new -key /tls/tls.key -subj "/CN=${REGISTRY_HOST}" -out /tls/registry.csr

# SAN обязателен
cat > /tls/registry.ext <<EOF
subjectAltName = DNS:${REGISTRY_HOST}
extendedKeyUsage = serverAuth
keyUsage = digitalSignature,keyEncipherment
EOF

openssl x509 -req -in /tls/registry.csr \
  -CA /tls/ca.crt -CAkey /tls/ca.key -CAcreateserial \
  -out /tls/tls.crt -days "${CERT_DAYS}" -sha256 -extfile /tls/registry.ext

rm -f /tls/registry.csr /tls/registry.ext

# права (на всякий случай)
chmod 600 /tls/tls.key /tls/ca.key || true
chmod 644 /tls/tls.crt /tls/ca.crt || true

# --- 3) финальная проверка, чтобы init не “успешно” завершался без артефактов ---
test -s /tls/ca.crt -a -s /tls/tls.crt -a -s /tls/tls.key

echo "[tls-init] Done."
