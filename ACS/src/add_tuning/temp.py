import time
import json
import os.path
import logging
import subprocess
from asyncio import get_event_loop

from celery import chain
from sqlalchemy import select

from src.models import stands, versions, repos
from src.database import get_async_session
from src.tasks.tasks import celery
from src.utils.secondary_func import socket_available, remote_cmd


from src.clonezilla_snap.clonezilla_func import backup_image

@celery.task
def temp_task():
    time.sleep(100)


class BootOrder:
    def __init__(self,
                 stand=None,
                 boottype='PXE'):
        
        self.stand = stand
        self.boot_type = boottype
        self.show_config = 'show /system1/bootconfig1/oemhp_uefibootsource'
        self.set_new_config = 'set /system1/bootconfig1/oemhp_uefibootsource{} bootorder=1'
        self.old_mode_key = '-oKexAlgorithms=+diffie-hellman-group1-sha1'
        self.no_fprint = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
        self.reset_machine = 'reset /system1'
        self.slot_count = 5
        if os.path.isfile('ilo.json'):
            with open('ilo.json', 'r') as ilocfg:
                self.ilo = json.load(ilocfg)
        self.login = self.ilo[self.stand]['username']
        self.password = self.ilo[self.stand]['password']
        self.address = self.ilo[self.stand]['ip']
        self.ssh_command = f'sshpass -p "{self.password}" ssh {self.no_fprint} {self.old_mode_key} -l {self.login} {self.address}'

    def cmd(self, cmd):
        output = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
        return output

    def set_boot_order(self):        
        try:
            for i in range(0, self.slot_count + 1, 1):
                answer = self.cmd(f'{self.ssh_command} {self.show_config}{i}')
                if self.boot_type in answer and i == 1:
                    logging.debug(f'\033[93m{self.boot_type} загрузка уже в приоритете, настройка не требуется\033[0m\n')
                    break
                elif self.boot_type in answer and i != 1:
                    logging.debug(f'\033[93m{answer}\033[0m')
                    result = self.cmd(f'{self.ssh_command} {self.set_new_config}'.format(i))
                    if 'Bootorder being set' in result:
                        logging.debug(f'\033[92mПриоритет загрузки успешно изменен на {self.boot_type}\033[0m\n')
                    break
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')

    def reset_by_timer(self, func):
        timer = 7200
        interval = 60
        for _ in range(timer // interval):
            time.sleep(interval)
            if not func.is_alive():
                return 0
        logging.debug(f'Время ожидания {timer} сек. Истекло, будет выполнена перезагрузка')
        logging.debug(self.cmd(f'{self.ssh_command} {self.reset_machine}'))


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
        print("OK 5")
    return {"Репозитории изменены"}

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
    """
        TODO Дописать преобразование
    """
    if stand[1] == "LowServer":
        num_stand = "stand3"
    elif stand[1] == "MiddleServer":
        num_stand = "stand4"
    elif stand[1] == "HighServer":
        num_stand = "stand5"
    change_boot_order = BootOrder(stand=num_stand)
    change_boot_order.set_boot_order()

    remote_cmd(command="sudo reboot", host=stand[3], user=stand[4], passwd=stand[5])
    time.sleep(15)
    socket_available(stand_ip=stand[3], user=stand[4], passwd=stand[5])
    time.sleep(15)

