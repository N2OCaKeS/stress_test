#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

usage() {
    cat <<EOF
Usage:
  $(basename "$0") [bash|zsh] [output-file]
EOF
}

shell_name="${1:-}"
output_file="${2:-}"

case "$shell_name" in
    bash|zsh)
        ;;
    *)
        usage >&2
        exit 1
        ;;
esac

pick_python() {
    for candidate in \
        "${PROJECT_DIR}/bootstrap/python/bin/python3.12" \
        "${PROJECT_DIR}/bootstrap/python/bin/python3" \
        "$(command -v python3 2>/dev/null || true)"
    do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

python_bin=$(pick_python || true)
if [ -z "${python_bin:-}" ]; then
    echo "Python interpreter for completion generation was not found." >&2
    exit 1
fi

complete_var="_ALLTA_COMPLETE"
generate_cmd() {
    env "${complete_var}=${shell_name}_source" PYTHONPATH="${PROJECT_DIR}" "$python_bin" -m allta_cli
}

if [ -n "$output_file" ]; then
    mkdir -p "$(dirname "$output_file")"
    generate_cmd >"$output_file"
else
    generate_cmd
fi
