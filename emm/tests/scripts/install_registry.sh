#!/usr/bin/env bash
# Download and install the distribution/registry binary for local E2E tests.
# Does nothing if already installed.

set -euo pipefail

VERSION="2.8.3"
ARCH="linux_amd64"
DEST="${HOME}/.local/bin/registry"

if command -v registry &>/dev/null; then
    echo "registry already installed: $(registry --version 2>&1)"
    exit 0
fi

mkdir -p "$(dirname "$DEST")"
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

URL="https://github.com/distribution/distribution/releases/download/v${VERSION}/registry_${VERSION}_${ARCH}.tar.gz"
# Note: v2.8.x uses docker/distribution, v3.x uses distribution/distribution.
# We use v2.x because registry:2 Docker image is based on it and accepts RSA public keys.
echo "Downloading registry ${VERSION}..."
curl -fsSL "$URL" -o "$TMP/registry.tar.gz"
tar -xzf "$TMP/registry.tar.gz" -C "$TMP" registry
cp "$TMP/registry" "$DEST"
chmod +x "$DEST"
echo "Installed: $DEST"
registry --version 2>&1
