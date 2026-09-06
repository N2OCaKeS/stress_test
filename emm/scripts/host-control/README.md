# emm host service control (ALLTA systemd units)

Lets `server_service` show live status of, and start/stop/restart, the ALLTA
systemd units on its own host (see `allta_app/allta_image_conf.py:allta_services_list`
for the origin of the unit list). This is deliberately built on plain SSH +
a forced command + explicit sudoers lines — the same trust model this
project already uses everywhere else for host operations — rather than
mounting the host's D-Bus/systemd socket into the container, which would need
either running the container as root or relaxing its security context far
more broadly than this one feature needs.

## What emm needs from the host

A dedicated, unprivileged local account (`emm-host-control` below, pick any
name) that:

1. Accepts SSH key auth from the key emm will use (generate a fresh
   ed25519 keypair for this — don't reuse any other emm key).
2. Can run exactly nothing except the forced command below.
3. Can `sudo systemctl {start,stop,restart}` the 12 allowed units, and
   `systemctl is-active` them without sudo (read-only, no privilege needed).

## Setup (run on the emm/ACS/DRBL host, as root)

```bash
useradd --system --shell /usr/sbin/nologin --create-home emm-host-control
install -o root -g root -m 0755 emm-host-service-guard.sh /usr/local/bin/emm-host-service-guard.sh
install -o root -g root -m 0440 90-emm-host-control /etc/sudoers.d/90-emm-host-control
visudo -c   # verify the sudoers file parses before trusting it

# Generate the keypair emm will hold (do this once, then paste the PRIVATE
# key into the emm admin UI at /admin/services.host_services — it's stored
# encrypted, write-only, never displayed again after save):
ssh-keygen -t ed25519 -f /tmp/emm-host-control-key -C emm-host-control -N ""

mkdir -p /home/emm-host-control/.ssh
cat >> /home/emm-host-control/.ssh/authorized_keys <<EOF
command="/usr/local/bin/emm-host-service-guard.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $(cat /tmp/emm-host-control-key.pub)
EOF
chown -R emm-host-control:emm-host-control /home/emm-host-control/.ssh
chmod 700 /home/emm-host-control/.ssh
chmod 600 /home/emm-host-control/.ssh/authorized_keys

# Paste /tmp/emm-host-control-key (the PRIVATE half) into the emm admin UI,
# then delete both local copies:
shred -u /tmp/emm-host-control-key /tmp/emm-host-control-key.pub
```

Then in emm: **Admin → Управление → Host services** (account_admin only),
set host `127.0.0.1` (or the host's real IP if emm connects to it over the
network rather than loopback), port `22`, user `emm-host-control`, and paste
the private key. Save — the ALLTA table on `/health` should go from
"не настроено" to real per-unit status within one 60s poll cycle.

## Verify manually before trusting the UI

```bash
ssh -i /path/to/private/key emm-host-control@<host> "systemctl is-active acs.service"
ssh -i /path/to/private/key emm-host-control@<host> "restart acs.service"   # should be REJECTED — not the forced format
ssh -i /path/to/private/key emm-host-control@<host> "systemctl restart nginx.service"   # should be REJECTED — not in allowlist
```
The first should print `active`/`inactive`/`failed`; the other two must print
the guard's rejection message on stderr and exit non-zero. If either of the
last two actually runs, stop and fix the sudoers/guard-script installation
before pointing emm at this account.

## Adding a unit to the allowlist later

Three places must agree, or the new unit silently won't work end-to-end:
`emm-host-service-guard.sh`'s `ALLOWED_UNITS`, this directory's sudoers
snippet (one `start`/`stop`/`restart` line, no wildcards), and
`ALLTA_HOST_UNITS` in `server_service/src/api/v1/endpoints/host_services.py`.
