# emm host service control — per-department

Lets a department's own admins (department_admin, or anyone holding the
`admin` service-role for `server_service` in that department) see live
status of, and start/stop/restart, systemd units on **their own** host from
inside emm. Every department configures this independently in **Admin →
Управление → Host services** (only visible/editable to that department's own
admins) — its own host, its own SSH key, its own chosen list of unit names.
There is no platform-wide roster and no `account_admin` visibility: a
department's service names and their status are private to that department.

This is built on plain SSH + a forced command + explicit sudoers — the same
trust model this project already uses everywhere else for host operations —
rather than mounting the host's D-Bus/systemd socket into the container,
which would need either running the container as root or relaxing its
security context far more broadly than this one feature needs.

## What each department needs on its own host

A dedicated, unprivileged local account (`emm-host-control` below, pick any
name — it only needs to be consistent between this setup and what you enter
in the emm admin UI) that:

1. Accepts SSH key auth from a key generated just for this department.
2. Can run exactly nothing except the forced command below.
3. Can `sudo systemctl {start,stop,restart}` only the units THIS department
   chose to expose, and `systemctl is-active` any unit without sudo
   (read-only, no privilege needed — the guard script still shapes the
   command, just doesn't need sudo for reads).

## Setup (run on the department's own host, as root)

```bash
useradd --system --shell /usr/sbin/nologin --create-home emm-host-control
install -o root -g root -m 0755 emm-host-service-guard.sh /usr/local/bin/emm-host-service-guard.sh

# List the systemd units THIS department wants emm to see/control — must
# match, one-for-one, what you'll add in the admin UI's unit list.
./gen_sudoers.sh acs.service allta_auth.service allta_infocollector.service \
  > /tmp/90-emm-host-control
install -o root -g root -m 0440 /tmp/90-emm-host-control /etc/sudoers.d/90-emm-host-control
visudo -c   # verify the sudoers file parses before trusting it
shred -u /tmp/90-emm-host-control

# Generate the keypair this department's emm settings page will hold (do
# this once, then paste the PRIVATE key into that admin UI — it's stored
# encrypted, write-only, never displayed again after save):
ssh-keygen -t ed25519 -f /tmp/emm-host-control-key -C emm-host-control -N ""

mkdir -p /home/emm-host-control/.ssh
cat >> /home/emm-host-control/.ssh/authorized_keys <<EOF
command="/usr/local/bin/emm-host-service-guard.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat /tmp/emm-host-control-key.pub)
EOF
chown -R emm-host-control:emm-host-control /home/emm-host-control/.ssh
chmod 700 /home/emm-host-control/.ssh
chmod 600 /home/emm-host-control/.ssh/authorized_keys

# Paste /tmp/emm-host-control-key (the PRIVATE half) into the emm admin UI
# (Admin → Управление → Host services, your own department), then delete
# both local copies:
shred -u /tmp/emm-host-control-key /tmp/emm-host-control-key.pub
```

Then in emm: **Admin → Управление → Host services** — set host `127.0.0.1`
(or the host's real IP if emm reaches it over the network rather than
loopback), port `22`, user `emm-host-control`, paste the private key, and
add each unit (e.g. `acs.service`) to the department's unit list — same
names you passed to `gen_sudoers.sh`. Save — the ALLTA table on `/health`
should go from "не настроено" / empty to real per-unit status within one
60s poll cycle.

## Verify manually before trusting the UI

```bash
ssh -i /path/to/private/key emm-host-control@<host> "systemctl is-active acs.service"
ssh -i /path/to/private/key emm-host-control@<host> "restart acs.service"   # should be REJECTED — not the forced format
ssh -i /path/to/private/key emm-host-control@<host> "systemctl restart nginx.service"   # should be REJECTED unless you explicitly added nginx.service to sudoers
```
The first should print `active`/`inactive`/`failed`; the other two must be
rejected (the guard script for a malformed command, sudo itself for a unit
missing from sudoers) and exit non-zero. If either of the last two actually
runs, stop and fix the sudoers/guard-script installation before pointing
emm at this account.

## Changing the unit list later

Two places must agree, or a unit silently won't work end-to-end: the
department's unit list in the emm admin UI, and that department's host
sudoers file (regenerate with `gen_sudoers.sh` and reinstall — the guard
script itself never needs to change, it has no embedded unit list).
