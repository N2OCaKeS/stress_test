from celery import chain
from celery_app import celery_app
from utils.ssh import SimpleSSH
from utils.scp import SCP  # если не нужен — убери
import json

@celery_app.task(bind=True)
def init(host: str, username: str, password: str, bridge: str, phy_if: str):
    ssh = SimpleSSH(host=host, username=username, password=password, port=22)

    # 1) Установка пакета
    allta_app = ""  # TODO: ссылка/путь
    if allta_app:
        ssh.run_command(f"wget -P /tmp {allta_app}")
        ssh.run_command("sudo dpkg -i /tmp/allta-vm*.deb")

    # 2) Инициализация сервера
    ssh.run_command(f"sudo allta-vm server init --phy-if {phy_if} --ip {host}")

@celery_app.task(bind=True)
def base_vm(host: str, username: str, password: str):
    ssh = SimpleSSH(host=host, username=username, password=password, port=22)
    ssh.run_command("sudo allta-vm vm base-create")
    return 0

@celery_app.task(bind=True)
def init_base_vm(host: str, username: str, password: str, bridge: str, phy_if: str):
    # Выполнить по очереди: сначала init, затем base_vm
    workflow = chain(
        init.s(host, username, password, bridge, phy_if),
        base_vm.s(host, username, password),
    )
    workflow.delay()
