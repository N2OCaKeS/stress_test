#!/bin/sh
set -eu

KEY_DIR="${REGISTRY_KEYS_DIR:-/registry-keys}"
KEY_FILE="${REGISTRY_KEY_FILE:-$KEY_DIR/registry_signing.key}"
CERT_FILE="${REGISTRY_CERT_FILE:-$KEY_DIR/auth-registry.crt}"
CERT_SUBJECT="${REGISTRY_CERT_SUBJECT:-/CN=allta.devos.astralinux.ru}"
CERT_DAYS="${REGISTRY_CERT_DAYS:-3650}"

mkdir -p "$KEY_DIR"

if [ ! -s "$KEY_FILE" ]; then
  echo "[allta-auth-init] Generating private key: $KEY_FILE"
  openssl genrsa -out "$KEY_FILE" 4096
else
  echo "[allta-auth-init] Private key already exists: $KEY_FILE"
fi

if [ ! -s "$CERT_FILE" ]; then
  echo "[allta-auth-init] Generating self-signed certificate: $CERT_FILE"
  openssl req -new -x509 \
    -key "$KEY_FILE" \
    -out "$CERT_FILE" \
    -days "$CERT_DAYS" \
    -subj "$CERT_SUBJECT"
else
  echo "[allta-auth-init] Certificate already exists: $CERT_FILE"
fi

chmod 600 "$KEY_FILE"
chmod 644 "$CERT_FILE"

echo "[allta-auth-init] Done."
