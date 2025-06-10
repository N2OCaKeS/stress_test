from ..._libs._ssh_command import _SSH_Command as ssh_command
from ...._decorators.Decorators import BaseDecorators
import threading
import time


class _AptManager:  
    """
    Класс для управления пакетами на виртуальных машинах через apt.

    Основные функции:
    - Установка пакетов на хостах или группах хостов.
    - Удаление пакетов на хостах или группах хостов.
    - Переустановка пакетов на хостах или группах хостов.

    Этот класс поддерживает многопоточное выполнение для одновременной работы с несколькими хостами.
    """
    @staticmethod
    @BaseDecorators.trycorator    
    def install(apt_structure: dict, vms_dates: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int:
        """
        Устанавливает пакеты на указанных хостах или группах хостов.

        Args:
            apt_structure (dict): Словарь с пакетами для установки.
                apt_structure = {
                    'database' = ['package'],
                    'g_group1' = ['package'] # Если выполнять на группе хостов необходимо указать в виде g_<groupname>
                }
            vms_date (list): Полная информация о виртуальных машинах.
                
                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }                        
                    }
            vms_groups (dict, optional): Группы виртуальных машин.
                
                vms_groups = {
                    'group1':['hostname1', 'hostname2'],
                    }
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".

        Returns:
            int: Код завершения выполнения.
        """
        def _threaded_install(host: str, packages: list):
            # Формирование команды: обновление репозиториев и установка пакетов
            cmd = f"sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(packages)}"
            ssh_command.cmd(
                host=host,
                command=cmd,
                username=username,
                password=password,
                vm_dates=vms_dates,
                task_name=f"apt install {packages}"
            )
        # Обрабатываем блок за блоком
        for target, packages in apt_structure.items():
            block_threads = []
            if target.startswith("g_"):
                group_name = target[2:]
                if vms_groups and group_name in vms_groups:
                    for host in vms_groups[group_name]:
                        thread = threading.Thread(target=_threaded_install, args=(host, packages))
                        block_threads.append(thread)
                        thread.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                host = target
                thread = threading.Thread(target=_threaded_install, args=(host, packages))
                block_threads.append(thread)
                thread.start()

            # Ожидание завершения всех потоков текущего блока
            for thread in block_threads:
                thread.join()

        return 0


    @staticmethod
    def remove(apt_structure: dict, vms_dates: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int:
        """
        Удаляет пакеты на указанных хостах или группах хостов.

        Args:
            apt_structure (dict): Словарь с пакетами для удаления.
                apt_structure = {
                    'database' = ['package'],
                    'g_group1' = ['package'] # Если выполнять на группе хостов необходимо указать в виде g_<groupname>
                }
            vms_date (list): Полная информация о виртуальных машинах.
                
                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }                        
                    }
            vms_groups (dict, optional): Группы виртуальных машин.
                
                vms_groups = {
                    'group1':['hostname1', 'hostname2'],
                    }
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".

        Returns:
            int: Код завершения выполнения.
        """
        def _threaded_remove(host: str, packages: list):
            cmd = f"sudo apt-get remove -y {' '.join(packages)}"
            ssh_command.cmd(
                host=host,
                command=cmd,
                username=username,
                password=password,
                vm_dates=vms_dates,
                task_name=f"apt remove on {host}"
            )

        for target, packages in apt_structure.items():
            block_threads = []
            if target.startswith("g_"):
                group_name = target[2:]
                if vms_groups and group_name in vms_groups:
                    for host in vms_groups[group_name]:
                        thread = threading.Thread(target=_threaded_remove, args=(host, packages))
                        block_threads.append(thread)
                        thread.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                host = target
                thread = threading.Thread(target=_threaded_remove, args=(host, packages))
                block_threads.append(thread)
                thread.start()

            for thread in block_threads:
                thread.join()

        return 0


    @staticmethod
    def reinstall(apt_structure: dict, vms_dates: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int:
        """
        Переустанавливает пакеты на указанных хостах или группах хостов.

        Args:
            apt_structure (dict): Словарь с пакетами для переустановки.
                apt_structure = {
                    'database' = ['package'],
                    'g_group1' = ['package'] # Если выполнять на группе хостов необходимо указать в виде g_<groupname>
                }
            vms_date (list): Полная информация о виртуальных машинах.
                
                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }                        
                    }
            vms_groups (dict, optional): Группы виртуальных машин.
                
                vms_groups = {
                    'group1':['hostname1', 'hostname2'],
                    }
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".

        Returns:
            int: Код завершения выполнения.
        """
        def _threaded_reinstall(host: str, packages: list):
            cmd = f"sudo apt-get install --reinstall -y {' '.join(packages)}"
            ssh_command.cmd(
                host=host,
                command=cmd,
                username=username,
                password=password,
                vm_dates=vms_dates,
                task_name=f"apt reinstall on {host}"
            )

        for target, packages in apt_structure.items():
            block_threads = []
            if target.startswith("g_"):
                group_name = target[2:]
                if vms_groups and group_name in vms_groups:
                    for host in vms_groups[group_name]:
                        thread = threading.Thread(target=_threaded_reinstall, args=(host, packages))
                        block_threads.append(thread)
                        thread.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                host = target
                thread = threading.Thread(target=_threaded_reinstall, args=(host, packages))
                block_threads.append(thread)
                thread.start()

            for thread in block_threads:
                thread.join()

        return 0
