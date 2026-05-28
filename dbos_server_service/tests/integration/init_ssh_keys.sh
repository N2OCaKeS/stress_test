#!/bin/sh
# Generate an ed25519 management keypair for E2E integration tests.
# Runs as a one-shot container; writes:
#   /keys/id_ed25519       — private key (chmod 600)
#   /keys/id_ed25519.pub   — public key
#
# Mounted into worker and test-runner via the `mgmt-keys` volume.
# Idempotent: if both files already exist, exits without regenerating.

set -eu

KEY=/keys/id_ed25519
PUB=/keys/id_ed25519.pub

if [ -s "$KEY" ] && [ -s "$PUB" ]; then
    echo "[init-ssh-keys] keypair already present, skipping"
    exit 0
fi

# alpine: openssh-keygen lives in openssh-keygen package; alpine image
# `alpine:3` has busybox ssh-keygen via the `openssh-keygen` apk.
if ! command -v ssh-keygen >/dev/null 2>&1; then
    apk add --no-cache openssh-keygen >/dev/null
fi

ssh-keygen -t ed25519 -N "" -C "dbos-test-mgmt" -f "$KEY"
chmod 600 "$KEY"
chmod 644 "$PUB"

echo "[init-ssh-keys] generated:"
ls -l /keys
echo "[init-ssh-keys] pubkey:"
cat "$PUB"
