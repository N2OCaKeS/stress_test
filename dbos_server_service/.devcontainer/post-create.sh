#!/usr/bin/env bash
set -euo pipefail

cd /workspace/dbos_server_manager

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
