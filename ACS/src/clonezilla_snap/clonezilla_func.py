import json
import os.path
import logging
import subprocess
import redfish

from celery import states
from time import time, sleep
from fabric import Connection
from paramiko import ssh_exception
from src.utils.secondary_func import remote_cmd, socket_available, busy_status_off, convert_stand_name
from src.clonezilla_snap.conf import RESTORE_DISK_COMMAND, SAVE_DISK_COMMAND
from src.tasks.tasks import celery


class CustomException(Exception):
    """Custom exception for specific error handling."""
    pass

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
        if os.path.isfile('/fastapi_app/src/ilo/ilo.json'):
            with open('/fastapi_app/src/ilo/ilo.json', 'r') as ilocfg:
                self.ilo = json.load(ilocfg)
        self.login = self.ilo[self.stand]['username']
        self.password = self.ilo[self.stand]['password']
        self.address = self.ilo[self.stand]['ip']
        self.ssh_command = f'sshpass -p "{self.password}" ssh {self.no_fprint} {self.old_mode_key} -oHostKeyAlgorithms=+ssh-rsa -l {self.login} {self.address}'
        self.client = redfish.RedfishClient(base_url=self.address, username=self.login, password=self.password)
        
        self.logger = logging.getLogger(name="BootOrder")
        self.logger.setLevel(logging.DEBUG)

    def cmd(self, cmd):
        output = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
        return output
    
    def set_boot_order(self):
        if self.stand == 'stand3' or self.stand == 'stand4':
            self.__set_boot_order_ilo()
        elif self.stand == 'stand5':
            self.__set_boot_order_idrac()

    def __set_boot_order_ilo(self):        
        try:
            for i in range(0, self.slot_count + 1, 1):
                answer = self.cmd(f'{self.ssh_command} {self.show_config}{i}')
                self.logger.info(f"COMMAND = {self.ssh_command} {self.show_config}{i}")
                self.logger.info(f"ANSWER = {answer}")
                if self.boot_type in answer and i == 1:
                    self.logger.debug(f'\033[93m{self.boot_type} загрузка уже в приоритете, настройка не требуется\033[0m\n')
                    break
                elif self.boot_type in answer and i != 1:
                    self.logger.debug(f'\033[93m{answer}\033[0m')
                    result = self.cmd(f'{self.ssh_command} {self.set_new_config}'.format(i))
                    if 'Bootorder being set' in result:
                        self.logger.debug(f'\033[92mПриоритет загрузки успешно изменен на {self.boot_type}\033[0m\n')
                    break
                else:
                    self.logger.info("В условия мы даже не зашли")
        except Exception as e:
            self.logger.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')

    def __set_boot_order_idrac(self):
        self.client.login(auth="session")

        try:
            response = self.client.get('/redfish/v1/Systems/System.Embedded.1')
            system_info = response.dict
            self.logger.debug("System Information: ", system_info)

            response = self.client.get('/redfish/v1/Systems/System.Embedded.1/BootSources')
            boot_sources = response.dict
            self.logger.debug("Boot Sources: ", boot_sources)

            body = {
                "Boot": {
                    "BootSourceOverrideTarget": "Pxe",
                    "BootSourceOverrideEnabled": "Once"
                }
            }
            response = self.client.patch('/redfish/v1/Systems/System.Embedded.1', body=body)
            if response.status == 200:
                self.logger.debug(f'\033[92mПриоритет загрузки успешно изменен на {self.boot_type}\033[0m\n')
        except Exception as e:
            self.logger.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')
        finally:
            self.client.logout()

    def __reboot_idrac(self):
        self.__set_boot_order_idrac()
        self.client.login(auth="session")

        try:
            body = {
                "ResetType": "ForceRestart"
            }
            response = self.client.post('/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset', body=body)
            if response.status in [200, 204]:
                self.logger.debug('execute IPMI iDRAC hard reboot successfully')
            else:
                self.logger.error(f'execute IPMI iDRAC hard reboot failed, status: {response.status}')
        except Exception as e:
            self.logger.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')
        finally:
            self.client.logout()

    def reset_by_timer(self, func):
        timer = 7200
        interval = 60
        for _ in range(timer // interval):
            sleep(interval)
            if not func.is_alive():
                return 0
        self.logger.debug(f'Время ожидания {timer} сек. Истекло, будет выполнена перезагрузка')
        if self.stand == 'stand3' or self.stand == 'stand4':
            self.logger.debug(self.cmd(f'{self.ssh_command} {self.reset_machine}'))
        elif self.stand == 'stand5':
            self.__reboot_idrac()

    def reset(self):
        if self.stand == 'stand3' or self.stand == 'stand4':
            self.logger.debug('execute IPMI hard reboot')
            self.logger.debug(self.cmd(f'{self.ssh_command} {self.reset_machine}'))
        elif self.stand == 'stand5':
            self.__reboot_idrac()


@celery.task
def backup_image(stand, snap_name: str, password_cs: str, restore=True, *args, **kwargs):

    """
        TODO Дописать преобразование
    """

    num_stand = convert_stand_name(stand_name=stand[1])
    
    change_boot_order = BootOrder(stand=num_stand)
    change_boot_order.set_boot_order()

    sleep(15)
    # print("test")

    start_time = time()
    # result = Connection("10.177.103.10", user="u", connect_kwargs={"password": "team13"}).run(command, hide=True)
    if restore:
        command = RESTORE_DISK_COMMAND.format(ip_address=stand[3], snapshot_name=snap_name, stand_disk=stand[2])
    else:
        command = SAVE_DISK_COMMAND.format(ip_address=stand[3], snapshot_name=snap_name, stand_disk=stand[2])
    logging.info(command)
    result = remote_cmd(command=command, host="10.177.103.10", user="u", passwd=password_cs, read=False)
    logging.info(result)
    logging.info(time() - start_time)
    sleep(15)
    change_boot_order.reset()
    socket_available(stand_ip=stand[3], user=stand[4], passwd=stand[5], cs_pass=password_cs)
    sleep(15)
    # return result


@celery.task(bind=True)
def get_snapshot(self, password_clonezilla_server: str, snap_name: str, *args, **kwargs):
    logging.info(f"password_clonezilla_server !!! = {password_clonezilla_server}")
    logging.info(f"SNAP NAME = {snap_name}")
    logging.info(f"ARGS = {args}")
    logging.info(f"KWARGS = {kwargs}")
    result = Connection("10.177.103.10", user="u", connect_kwargs={"password": f"{password_clonezilla_server}"}).run("ls /home/partimag", hide=True)
    snaps = list(filter(lambda x: x != "nohup.out", result.stdout.strip().split("\n")))
    if not snap_name in snaps:
        logging.info("ЗАШЛИ В УСЛОВИЕ, ЗНАЧИТ НЕ НАЙДЕН СНИМОК")
        self.update_state(state=states.FAILURE, meta={'exc_type': 'CustomException', 'exc': "Снимок не создался"})
    
    num_stand = convert_stand_name(stand_name=snap_name.split("-")[0])
    busy_status_off(stand=num_stand)
    
        
        