from backend.allta_vm_api.services.worker.celery_app import celery_app
from app.utils.ssh import SimpleSSH



@celery_app.task
def create(host, username, password, vms_dates, snapshot):
    ssh = SimpleSSH(host=host, username=username, password=password)

    cre
    pass

@celery_app.task
def delete(vms, snapshot):
    pass

@celery_app.task
def revert(vms, snapshot):
    pass


        