from __future__ import annotations
from celery import shared_task
from tasks.common import log, _require, _std_ok, _std_error
from utils.ssh import SimpleSSH
import json

@shared_task(name="task_vm_create", bind=True) # Work
def task_vm_create(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vms_full"])
    server   = envelope.get("server") or {}
    vms_full = envelope.get("vms_full") or {}
    json_remote_path = envelope.get("json_remote_path") or {}    
    box = "vm_station"
    rc = "1.7.5.9"

    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    export_vms = f'echo \'{json.dumps(vms_full)}\'| sudo tee {json_remote_path}'
    ssh.run_command(command=export_vms)
    
    # command = f"sudo allta-vm vm create --info-path {json_remote_path} --box {box} --rc {rc}"
    # out = ssh.run_command(command=command)
    # log.info(out)

    rm_command = f"sudo rm -rf {json_remote_path} /vms/vm_station.tar.gz /vms/vm_station.qcow2"
    ssh.run_command(command=rm_command)


    log.info("Ok vm.create RUN: task_id=%s server=%s vms=%s box=%s",
             envelope["task_id"], server, list(vms_full.keys()), box)
    return _std_ok(envelope)



@shared_task(name="task_vm_base_create", bind=True) # Work
def task_vm_base_create(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vms_full"])
    server   = envelope.get("server") or {}
    vms_full = envelope.get("vms_full") or {}
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    command = "sudo allta-vm vm base-create"
    ssh.run_command(command=command)
    log.info("Ok vm.base_create RUN: task_id=%s server=%s vms=%s",
             envelope["task_id"], server, list(vms_full.keys()))
    return _std_ok(envelope)



@shared_task(name="task_vm_update", bind=True) # Work
def task_vm_update(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vms_full"])
    server   = envelope.get("server") or {}
    patches = envelope.get("vms_full") or {}
    json_remote_path = envelope.get("json_remote_path") or {}        
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    export_vms = f'echo \'{json.dumps(patches)}\'| sudo tee {json_remote_path}'
    ssh.run_command(command=export_vms)


    command = f"sudo allta-vm vm update --info-path {json_remote_path}"
    ssh.run_command(command=command)
    log.info("Ok vm.update RUN: task_id=%s patches=%s", envelope["task_id"], patches)
    return _std_ok(envelope)



@shared_task(name="task_vm_delete", bind=True) # ?
def task_vm_delete(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vm_names"])
    server   = envelope.get("server") or {}
    vm_names = envelope.get("vm_names") or []
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    command = ["sudo allta-vm vm delete"]
    for v in vm_names:
        command.append(f" --vms {v}")
    cmd = " ".join(command)

    out = ssh.run_command(command=cmd)
    log.info(out)
    log.info("Ok vm.delete RUN: task_id=%s vm_names=%s", envelope["task_id"], vm_names)
    return _std_ok(envelope)



@shared_task(name="task_vm_start", bind=True) # Work
def task_vm_start(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vm_names"])
    server   = envelope.get("server") or {}
    vm_names = envelope.get("vm_names") or []
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    command = ["sudo allta-vm vm start"]
    for v in vm_names:
        command.append(f" --vms {v}")
    cmd = "".join(command)    
    ssh.run_command(command=cmd)

    log.info("Ok vm.start RUN: task_id=%s vm_names=%s", envelope["task_id"], vm_names)
    return _std_ok(envelope)



@shared_task(name="task_vm_stop", bind=True) # Work
def task_vm_stop(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vm_names"])
    server   = envelope.get("server") or {}    
    vm_names = envelope.get("vm_names") or []
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    command = ["sudo allta-vm vm stop"]
    for v in vm_names:
        command.append(f" --vms {v}")
    cmd = "".join(command)    
    ssh.run_command(command=cmd)  

    log.info("Ok vm.stop RUN: task_id=%s vm_names=%s", envelope["task_id"], vm_names)
    return _std_ok(envelope)



@shared_task(name="task_vm_astra_update", bind=True)
def task_vm_astra_update(self, envelope: dict) -> dict:
    _require(envelope, ["task_id", "operation", "server", "vm_names", "vms_full", "rc"])
    server   = envelope.get("server") or {}    
    rc       = envelope.get("rc")
    vms_full = envelope.get("vms_full") or []
    json_remote_path = envelope.get("json_remote_path") or {}     
    ssh = SimpleSSH(host=server["ip"], username=server["username"], password=server["password"])

    export_vms = f'echo \'{json.dumps(vms_full)}\'| sudo tee {json_remote_path}'
    ssh.run_command(command=export_vms)

    command = f"sudo allta-vm vm astra-update --info-path {json_remote_path} --rc {rc}"
    out = ssh.run_command(command=command)
    log.info(out)
    log.info("Ok vm.astra_update RUN: task_id=%s rc=%s vms_full=%s",
             envelope["task_id"], rc, vms_full)
    return _std_ok(envelope)

# sudo allta-vm vm delete  --vms additionalProp1111111111