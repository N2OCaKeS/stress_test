import time
import json
import os.path
import logging
import subprocess
import requests
from asyncio import get_event_loop

from celery import chain
from sqlalchemy import select

from src.models import stands, versions, repos
from src.database import get_async_session
from src.tasks.tasks import celery
from src.utils.secondary_func import socket_available, remote_cmd

from src.add_tuning.conf import COMPONENTS_INSTALL

@celery.task
def temp_task():
    time.sleep(100)


async def change_repos(version_name, stand):
    print("OK 2")
    # session = await get_async_session()
    async for session in get_async_session():
        query_version = select(versions).where(versions.c.name == version_name)
        version = await session.execute(query_version)
        version = version.first()
        print("OK 3")
        # print(version[0])
        query_repo = select(repos).where(repos.c.version_id == version[0])
        repo = await session.execute(query_repo)
        repo = repo.mappings().all()[0].get('link')
        print("OK 4")
        # print(repo)
        command = f'sudo echo "{repo}" | sudo tee /etc/apt/sources.list > /dev/null && sudo apt update -y'
        remote_cmd(command=command, host=stand[3], user=stand[4], passwd=stand[5])
        priority_command = """
            cat << EOF | sudo tee /etc/apt/preferences.d/devel
            Package: *
            Pin: release l=devel
            Pin-Priority: 500

            Package: *
            Pin: release l=extended
            Pin-Priority: 500
            EOF
            sudo apt update -y
        """
        remote_cmd(command=priority_command, host=stand[3], user=stand[4], passwd=stand[5])
        print("OK 5")
    return {"Репозитории изменены"}

def install_kernels(version_name, stand):
    # if version_name.startswith("1.7.2"):
    #     kernel = "linux-5.15-generic"
    # elif version_name.startswith("1.7.3") or version_name.startswith("174"):
    #     kernel = "linux-5.15-generic linux-5.15-lowlatency"
    # elif version_name.startswith("1.7.5"):
    #     kernel = "linux-5.15-generic linux-5.15-lowlatency linux-6.1-generic"
    # elif version_name.startswith("1.7.6"):
    #     kernel = "linux-5.15-generic linux-5.15-lowlatency linux-6.1-generic"
    # # временное решение
    # elif version_name.startswith("1.7.7"):
    #     kernel = "linux-5.15-generic linux-5.15-lowlatency linux-6.1-generic"
    # elif version_name.startswith("1.8.1"):
    #     kernel = "linux-6.6-generic"
    # else:
    #     kernel = None
    
    res_get_kernel = requests.post(f"http://allta.devos.astralinux.ru/rest/api/available-kernels-from-{version_name}", data={"rc": version_name})
    if res_get_kernel.status_code == 200:
        data = res_get_kernel.json()
        kernel = " ".join(data)
        print(f"ЯДРА ДЛЯ УСТАНОВКИ - {kernel}")
    else:
        print("API KERNEL не отработало")
    
    components = " ".join(COMPONENTS_INSTALL)
    install_components = remote_cmd(command=f"sudo apt update && sudo apt install -y {components}", 
                                    host=stand[3], 
                                    user=stand[4], 
                                    passwd=stand[5])
    print(install_components)
    if kernel:
        command = f"sudo apt install -y {kernel}"
        print(command)
        data = remote_cmd(command=command, host=stand[3], user=stand[4], passwd=stand[5])
        print(data)
        print("ЯДРА ДОЛЖНЫ БЫЛИ УСТАНОВИТЬСЯ")
        return {"ok"}
    else:
        return {"Нет доп ядер"}

@celery.task
def astra_version_update(new_version: str, stand, *args, **kwargs):
    logging.error(f"{new_version} new_version!!!")
    logging.error(f"{stand} stand!!!")
    logging.error(f"{args} args!!!")
    logging.error(f"{kwargs} kwargs!!!")
    loop = get_event_loop()
    res = loop.run_until_complete(change_repos(new_version, stand))
    logging.info(res)
    command_update = "sudo astra-update -A -T -r"
    # command_update = "touch ttest.txt"
    remote_cmd(command=command_update, host=stand[3], user=stand[4], passwd=stand[5])
    # """
    #     TODO Дописать преобразование
    # """
    # if stand[1] == "LowServer": 
    #     num_stand = "stand3"
    # elif stand[1] == "MiddleServer":
    #     num_stand = "stand4"
    # elif stand[1] == "HighServer":
    #     num_stand = "stand5"
    # change_boot_order = BootOrder(stand=num_stand)
    # change_boot_order.set_boot_order()
    install_kernels(version_name=new_version, stand=stand)
    remote_cmd(command="sudo reboot", host=stand[3], user=stand[4], passwd=stand[5])
    time.sleep(15)
    socket_available(stand_ip=stand[3], user=stand[4], passwd=stand[5])
    time.sleep(15)