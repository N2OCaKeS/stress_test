#!/usr/bin/env bash
# Скачать self-hosted статику noVNC и spice-html5 в static/assets/.
# Вызывается при сборке образа (см. Dockerfile). Версии пинятся, чтобы образ
# был воспроизводим и CSP не тянул ничего с CDN — всё раздаётся с того же origin.
set -euo pipefail

NOVNC_VERSION="${NOVNC_VERSION:-1.5.0}"
SPICE_VERSION="${SPICE_VERSION:-0.3.0}"

DEST="$(cd "$(dirname "$0")" && pwd)/static/assets"
mkdir -p "$DEST"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo ">> noVNC ${NOVNC_VERSION}"
curl -fsSL "https://github.com/novnc/noVNC/archive/refs/tags/v${NOVNC_VERSION}.tar.gz" \
  -o "$tmp/novnc.tgz"
mkdir -p "$tmp/novnc"
tar -xzf "$tmp/novnc.tgz" -C "$tmp/novnc" --strip-components=1
rm -rf "$DEST/novnc"
mkdir -p "$DEST/novnc"
# noVNC грузится как ES-модули из core/ + app/ + vendor/.
cp -r "$tmp/novnc/core" "$DEST/novnc/core"
cp -r "$tmp/novnc/vendor" "$DEST/novnc/vendor"

echo ">> spice-html5 ${SPICE_VERSION}"
curl -fsSL "https://gitlab.freedesktop.org/spice/spice-html5/-/archive/${SPICE_VERSION}/spice-html5-${SPICE_VERSION}.tar.gz" \
  -o "$tmp/spice.tgz"
mkdir -p "$tmp/spice"
tar -xzf "$tmp/spice.tgz" -C "$tmp/spice" --strip-components=1
rm -rf "$DEST/spice-html5"
mkdir -p "$DEST/spice-html5"
cp "$tmp/spice"/*.js "$DEST/spice-html5/" 2>/dev/null || true
if [ -d "$tmp/spice/thirdparty" ]; then
  cp -r "$tmp/spice/thirdparty" "$DEST/spice-html5/thirdparty"
fi

echo ">> done: $DEST"
