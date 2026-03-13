#!/bin/sh

set -eu

SCRIPT_NAME=$(basename "$0")

usage() {
    cat <<EOF
Usage:
  $SCRIPT_NAME [bash|zsh] [allta-command]

Examples:
  $SCRIPT_NAME bash
  $SCRIPT_NAME zsh allta

Notes:
  - POSIX sh itself does not support programmable completion.
  - The script installs completion for bash or zsh for the current user.
  - The command must already be available in PATH.
EOF
}

detect_shell() {
    if [ -n "${SHELL:-}" ]; then
        basename "$SHELL"
        return 0
    fi
    return 1
}

shell_name="${1:-}"
if [ "${shell_name:-}" = "-h" ] || [ "${shell_name:-}" = "--help" ]; then
    usage
    exit 0
fi

if [ -z "${shell_name:-}" ]; then
    shell_name=$(detect_shell || true)
fi

command_name="${2:-allta}"

case "$shell_name" in
    bash|zsh)
        ;;
    ""|sh)
        echo "Completion can be installed only for bash or zsh. Plain sh is not supported." >&2
        usage >&2
        exit 1
        ;;
    *)
        echo "Unsupported shell: $shell_name" >&2
        usage >&2
        exit 1
        ;;
esac

if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Command '$command_name' was not found in PATH." >&2
    exit 1
fi

complete_var=$(printf '%s' "$command_name" | tr '[:lower:]-' '[:upper:]_')
complete_var="_${complete_var}_COMPLETE"

generate_completion() {
    env "${complete_var}=${shell_name}_source" "$command_name"
}

append_once() {
    rc_file=$1
    marker=$2
    snippet=$3

    touch "$rc_file"
    if grep -F "$marker" "$rc_file" >/dev/null 2>&1; then
        return 0
    fi

    {
        printf '\n%s\n' "$marker"
        printf '%s\n' "$snippet"
    } >>"$rc_file"
}

if [ "$shell_name" = "bash" ]; then
    target_dir="${HOME}/.local/share/bash-completion/completions"
    target_file="${target_dir}/${command_name}"
    rc_file="${HOME}/.bashrc"
    marker="# allta completion"
    snippet="[ -f \"$target_file\" ] && . \"$target_file\""
else
    target_dir="${HOME}/.zfunc"
    target_file="${target_dir}/_${command_name}"
    rc_file="${HOME}/.zshrc"
    marker="# allta completion"
    snippet="fpath=(\"$target_dir\" \$fpath)\nautoload -Uz compinit\ncompinit"
fi

mkdir -p "$target_dir"
generate_completion >"$target_file"

append_once "$rc_file" "$marker" "$snippet"

echo "Installed $shell_name completion for '$command_name': $target_file"
echo "Shell startup updated: $rc_file"
echo "Open a new shell or run:"
if [ "$shell_name" = "bash" ]; then
    echo "  . \"$rc_file\""
else
    echo "  exec zsh"
fi
