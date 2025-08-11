from backend.allta_vm_api.services.worker.celery_app import celery_app
from allta import SystemCommands
from tasks import snapshot
from tasks.config import VIRSH, repo
import time



@celery_app.task
def create():
    pass

@celery_app.task
def delete(vm):
    snapshot.delete_all(vm)
    commands = f"{VIRSH} undefine --domain {vm} --"

@celery_app.task
def update(vm):
    task  = {
        vm: { 
            'prepare': {

            }
        }
    }
    pass

@celery_app.tasks
def astra_update(vm, rc):
    
    new_repo = repo(rc)
    repo_command = f"echo -e '{new_repo}' | sudo tee /etc/apt/sources.list"
    update_command = "sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive astra-update -A -T -r",
    pass

@celery_app.task
def start(vm):
    commands = f"{VIRSH} start --domain {vm}"


@celery_app.task
def stop(vm):
    commands = f"{VIRSH} destroy --domain {vm}"

