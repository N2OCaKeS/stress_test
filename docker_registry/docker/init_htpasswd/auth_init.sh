#!/usr/bin/env sh
set -euo pipefail

: "${REGISTRY_USER:?REGISTRY_USER is required}"
: "${REGISTRY_PASS:?REGISTRY_PASS is required}"

umask 077
mkdir -p /auth

# Не перетираем, чтобы пароль не менялся при каждом запуске
if [ -s /auth/htpasswd ]; then
  echo "[auth-init] htpasswd exists, skip."
else
  echo "[auth-init] Creating htpasswd for user: ${REGISTRY_USER}"
  # -B: bcrypt, -n: вывод в stdout (совместимость), -b: пароль аргументом
  htpasswd -Bbn "${REGISTRY_USER}" "${REGISTRY_PASS}" > /auth/htpasswd
  chmod 600 /auth/htpasswd || true
fi

# финальная проверка
test -s /auth/htpasswd

echo "[auth-init] Done."
