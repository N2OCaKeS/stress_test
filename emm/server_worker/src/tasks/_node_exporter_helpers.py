"""Общий installer node_exporter для server/VM задач и prepare."""

from __future__ import annotations

from src.clients.ssh import SshError

AUDIT_SAFE_FIELDS: set[str] = {
    "server_id",
    "vm_id",
    "vm_name",
    "target",
    "returncode",
}

_OUTPUT_TAIL_CHARS = 16_000

INSTALL_SCRIPT = r"""set -euo pipefail

NE_PATH=/home/node_exporter
sudo mkdir -p "$NE_PATH"
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "insecure-registries": ["allta.devos.astralinux.ru:21503"]
}
EOF

if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl restart docker.service || true
fi

sudo tee "$NE_PATH/docker-compose.yml" >/dev/null <<'EOF'
version: '3.7'

services:
  node_exporter:
    image: allta.devos.astralinux.ru:21503/prom/node-exporter:latest
    network_mode: host
EOF

if grep -q '1.8' /etc/astra_version 2>/dev/null || grep -qE '^(1\.7\.[1-9][0-9]+)$' /etc/astra_version 2>/dev/null; then
  COMPOSE_CMD="docker compose"
  COMPOSE_PKG="docker-compose-v2"
else
  COMPOSE_CMD="docker-compose"
  COMPOSE_PKG="docker-compose"
fi

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io "$COMPOSE_PKG"
fi

sudo tee /etc/systemd/system/node_exporter.service >/dev/null <<EOF
[Unit]
Description=Docker node exporter service
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$NE_PATH
ExecStart=/usr/bin/$COMPOSE_CMD up -d
ExecStop=/usr/bin/$COMPOSE_CMD down
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable node_exporter.service
sudo systemctl restart node_exporter.service
sudo systemctl status node_exporter.service --no-pager
"""


async def install_node_exporter(runner, target: str) -> dict:
    rc, stdout, stderr = await runner.run(
        "bash -s",
        sudo=True,
        stdin=INSTALL_SCRIPT,
    )
    output = (stdout or "") + (stderr or "")
    output_tail = output[-_OUTPUT_TAIL_CHARS:]
    if rc != 0:
        raise SshError(
            error_code="NODE_EXPORTER_INSTALL_FAILED",
            host=target,
            cmd_sanitized="sudo bash -s <install_node_exporter>",
            returncode=rc,
            stderr=output_tail,
        )
    return {
        "target": target,
        "returncode": rc,
        "output_tail": output_tail,
    }
