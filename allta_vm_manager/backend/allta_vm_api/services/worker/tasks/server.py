from __future__ import annotations
from celery import shared_task
from tasks.common import log, _require, _std_ok
from utils.ssh import SimpleSSH

def _run_or_raise(ssh: SimpleSSH, cmd: str, *, title: str, timeout: int = 600):
    log.info("→ RUN: %s", title)
    res = ssh.run_command(cmd, timeout=timeout, stream=lambda line: log.info("%s", line))
    log.info("← DONE: %s (rc=%s, %.2fs)", title, res["rc"], res["duration_s"])
    if res["rc"] != 0:
        log.error("Command failed: %s\nSTDOUT:\n%s\nSTDERR:\n%s", title, res["stdout"], res["stderr"])
        raise RuntimeError(f"remote command failed (rc={res['rc']}): {title}")
    return res

@shared_task(name="task_server_init", bind=True)
def task_server_init(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server"])
    server = envelope.get("server") or {}
    ip = server["ip"]; username = server["username"]; password = server["password"]; phy_if = server["phy_if"]

    ssh = SimpleSSH(host=ip, username=username, password=password)

    _run_or_raise(ssh, f"wget -q -O /tmp/allta_cli.deb ftp://10.177.103.10/boxes/allta-vm_1.0.0_amd64.deb",
                  title="download allta_cli.deb", timeout=300)

    _run_or_raise(ssh, "sudo dpkg -i /tmp/allta_cli.deb", title="install allta_cli", timeout=300)

    _run_or_raise(ssh, "sudo mkdir -p /opt/allta_vm/jobs && sudo chmod -R 0775 /opt",
                  title="prepare /opt/allta_vm", timeout=120)
    
    _run_or_raise(ssh, f"sudo allta-vm server init --phy-if {phy_if} --ip {ip}",
                  title="allta-vm server init", timeout=600)

    log.info("Ok server.init RUN: task_id=%s server=%s", envelope["task_id"], server)
    return _std_ok(envelope)

@shared_task(name="task_server_remove", bind=True)
def task_server_remove(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server"])
    server = envelope.get("server") or {}
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    del_deps = "sudo apt purge astra-kvm -y"
    ssh.run_command(command=del_deps)

    del_vms_path = "sudo rm -rf /vms"    
    ssh.run_command(command=del_vms_path)

    del_allta_cli = "sudo apt purge allta-vm -y"
    ssh.run_command(command=del_allta_cli)

    rm_path = "rm -rf /opt/allta_vm"
    ssh.run_command(command=rm_path)

    log.info("Ok server.remove RUN: task_id=%s server=%s", envelope["task_id"], server)
    return _std_ok(envelope)
