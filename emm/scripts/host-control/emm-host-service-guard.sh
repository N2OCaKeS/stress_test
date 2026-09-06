#!/usr/bin/env bash
# Forced command for the SSH key one department's emm backend uses to
# control THAT department's own systemd units on THEIR host. Installed via
# `command="..."` in that key's authorized_keys line (see README.md in this
# directory) — SSH ignores whatever command the client actually asked for
# and runs this script instead, with the client's request in
# $SSH_ORIGINAL_COMMAND.
#
# Each department has its own host, its own key, and its own copy of this
# script + a sudoers snippet naming exactly the units THAT department chose
# to expose (see 90-emm-host-control.template in this directory) — there is
# no cross-department shared allowlist here. This script only checks SHAPE
# (a well-formed `systemctl <verb> <unit>.service` call, nothing else can
# ever be smuggled through, no `;`, no `&&`, no extra args) — WHICH units are
# actually reachable is decided entirely by that host's own sudoers rules.
# is-active needs no privilege and runs directly; start/stop/restart go
# through sudo, so a unit missing from sudoers fails there even if it
# somehow got past this script (defense in depth, not the only barrier —
# the app itself also checks its own per-department stored unit list before
# ever sending a command here).
set -euo pipefail

cmd="${SSH_ORIGINAL_COMMAND:-}"

if [[ ! "$cmd" =~ ^systemctl\ (is-active|start|stop|restart)\ ([a-zA-Z0-9_.@-]+)\.service$ ]]; then
  echo "emm-host-service-guard: rejected command: $cmd" >&2
  exit 1
fi

action="${BASH_REMATCH[1]}"
unit="${BASH_REMATCH[2]}"

if [[ "$action" == "is-active" ]]; then
  exec systemctl is-active "${unit}.service"
else
  exec sudo /bin/systemctl "$action" "${unit}.service"
fi
