#!/usr/bin/env bash
# Forced command for the dedicated SSH key emm uses to control allta systemd
# units on its own host. Installed via `command="..."` in that key's
# authorized_keys line (see README.md in this directory) — SSH ignores
# whatever command the client actually asked for and runs this script
# instead, with the client's request in $SSH_ORIGINAL_COMMAND. This script
# is the ONLY thing standing between "emm backend has an SSH key" and
# "emm backend can run arbitrary shell on the host" — the app-side allowlist
# in server_service is a second, independent check, not a substitute for
# this one.
#
# Accepts exactly: systemctl (is-active|start|stop|restart) <unit>.service
# for one of the units below. Anything else is rejected.
set -euo pipefail

ALLOWED_UNITS="acs allta_auth allta_infocollector allta allta_vm changelog devpi grafana_prometheus node_exporter portainer statistics docker_registry"

cmd="${SSH_ORIGINAL_COMMAND:-}"

if [[ ! "$cmd" =~ ^systemctl\ (is-active|start|stop|restart)\ ([a-z_]+)\.service$ ]]; then
  echo "emm-host-service-guard: rejected command: $cmd" >&2
  exit 1
fi

action="${BASH_REMATCH[1]}"
unit="${BASH_REMATCH[2]}"

allowed=false
for u in $ALLOWED_UNITS; do
  if [[ "$u" == "$unit" ]]; then
    allowed=true
    break
  fi
done

if [[ "$allowed" != "true" ]]; then
  echo "emm-host-service-guard: unit not in allowlist: $unit" >&2
  exit 1
fi

# is-active needs no privilege; start/stop/restart go through the sudoers
# rule installed alongside this script (see sudoers.d snippet in this
# directory) — one explicit line per unit/action, no wildcards.
if [[ "$action" == "is-active" ]]; then
  exec systemctl is-active "${unit}.service"
else
  exec sudo /bin/systemctl "$action" "${unit}.service"
fi
