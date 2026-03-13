#!/bin/bash

set -euo pipefail

PY_ROOT="/opt/allta_cli/python"
PY_BIN="$PY_ROOT/bin/python3.12"

exec "$PY_BIN" -m allta_cli.__main__ "$@"
