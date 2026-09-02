#!/usr/bin/env bash
set -euo pipefail

cd /workspace/dbos_server_manager

# Allta devpi package index
sudo python3 -m pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release
sudo python3 -m pip config --global set global.trusted-host allta.devos.astralinux.ru

python -m pip install --upgrade pip

if [[ -f "auth_service/requirements.txt" ]]; then
  pip install -r auth_service/requirements.txt
fi

if [[ -f "web_settings/package.json" ]]; then
  cd /workspace/dbos_server_manager/web_settings
  npm install
  cd /workspace/dbos_server_manager
fi

bash .devcontainer/post-start.sh
