#!/bin/bash

set -euo pipefail

PY_ROOT="/opt/allta_cli/python"
PY_BIN="$PY_ROOT/bin/python3.12"
PY_LIB_DIR="$PY_ROOT/lib"

if [ -d "$PY_LIB_DIR" ]; then
    export ALLTA_EMBEDDED_LIB_DIR="$PY_LIB_DIR"
    export LD_LIBRARY_PATH="$PY_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

exec "$PY_BIN" -m allta_cli.__main__ "$@"
