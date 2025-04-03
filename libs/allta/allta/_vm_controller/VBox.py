from .._system_command.SystemCommands import SystemCommands as system_commands
from ._libs._ssh_comand import _SSH_Command as ssh_command
from ._libs._scp_comand import _SCP_Command as scp_command
from ._libs._signals import _Signals as signals

from ._base_commands._apt._apt_prorocol import _AptManagerProtocol
from ._base_commands._apt import _apt

from ._base_commands._reboot._reboot import _Reboot as reboot
from ._base_commands._sed._sed import _Sed as sed
from ._base_commands._set_hosts._set_hosts import _SetHosts as set_hosts
from ._base_commands._freeipa._freeipa import _Freeipa as freeipa


from ._vm._vagrant import _Vagrant
from ._vm._virtual_machine import _VirtualMashines
from ._vm._vbox_manage import _VboxManager as vbox_manager

from typing import cast

import threading


class VBox(_VirtualMashines):
    """
    Класс VBox предоставляет интерфейс для управления виртуальными машинами (ВМ) с использованием Vagrant и VirtualBox.

    Основные функции:
    - Подготовка окружения для работы с ВМ.
    - Создание и настройка ВМ на основе Vagrantfile.
    - Проверка доступности ВМ через ping.
    - Выполнение команд на ВМ.
    - Копирование файлов между локальной системой и ВМ.
    - Управление пакетами на ВМ через apt.
    - Настройка файла /etc/hosts на ВМ.

    Этот класс наследуется от абстрактного класса `_VirtualMashines` и реализует его методы.
    """
    @classmethod
    def prepare(cls, path_prepare) -> int:
        """
        Выполняет подготовку окружения для работы с виртуальными машинами.

        Args:
            path_prepare (str): Путь до файла скрипта подготовки.

        Returns:
            int: Код завершения выполнения команды.
        """
        return system_commands.cmd_with_returncode(f"sudo bash {path_prepare}")

    @classmethod
    def build(cls, path_to_vagrantfile: str, box: str, rc: str, vms: list, vms_date: list) -> int:
        """
        Создаёт и настраивает виртуальные машины на основе Vagrantfile.

        Args:
            path_to_vagrantfile (str): путь до папки где лежит vagrantfile
            box (str): Имя образа (бокса).
            rc (str): Версия операционной системы.
            vms (list): Список имён виртуальных машин.
            vms_date (list): Полная информация о виртуальных машинах.
                vm_dates = {'hostname':{
                    'host-port':'*',
                    'ip':'10.0.0.11', #  ip внутренней сети
                    'sshnum':'',
                    'ip_bridge':'*.*.*.*', # ip моста
                    'cpus':'*',
                    'memory':'*', # RAM
                    'disk':'*'}
                    } 

        Returns:
            int: Код завершения выполнения.
        """
        vagrant = _Vagrant(path_to_vagrantfile, box, rc, vms_date)

        vagrant.vagrant_up()

        vbox_manager.set_bridge_network(vms)
        vbox_manager.create_snapshots_all_vm(vms)
        system_commands.cmd('vboxmanage natnetwork list')
        system_commands.cmd('vboxmanage list hostonlyifs')
        system_commands.cmd('vboxmanage list bridgedifs')
        system_commands.cmd('vboxmanage list vms')

        # TODO Узнать нужен ли этот блок

        # if path.isfile('/home/iface/iface'):
        #     with open('/home/iface/iface', 'r') as r:
        #         if_name = r.read().strip()
        # else:
        #     if rc.startswith('1.7'):
        #         if_name = 'eth2'  #  Узнать правильные названия интерфейсов и указать их
        #     elif rc.startswith('1.8'):
        #         if_name = 'ens5'
        return 0

    @classmethod
    def check(cls, vms: list, vm_dates: dict):
        """
        Проверяет доступность виртуальных машин через ping.

        Args:
            vms (list): Список имён виртуальных машин.
            vm_dates (dict): Полная информация о виртуальных машинах.

        Returns:
            int: 0, если все машины доступны, иначе 1.
        """
        def _check_ping():
            bad_vms = [vm for vm in vms if system_commands.cmd_with_returncode(
                f"ping -c 1 {vm_dates[vm]['ip_bridge']}") != 0]
            return bad_vms

        if _check_ping():
            print("Не удалось решить проблемы с настройкой сети, ВМ недоступна(ы)")
            return 1
        else:
            print("All vms is available")
            return 0

    @classmethod
    def execute(cls, vm_dates: dict, commands: dict, vms_groups: dict = None,
                username: str = "u", password: str = "1") -> int:
        """
        Выполняет команды на виртуальных машинах. Если имя задачи равно "reboot", то производится
        перезагрузка с ожиданием готовности ВМ. При выполнении команды для группы ВМ перезагрузка
        производится для всей группы, и сигнал устанавливается только когда все ВМ из группы готовы.

        Args:
            vm_dates (dict): Информация о виртуальных машинах.
            commands (dict): Словарь с командами для выполнения.
            vms_groups (dict, optional): Группы виртуальных машин.
            username (str, optional): Имя пользователя для SSH.
            password (str, optional): Пароль для SSH.

        Returns:
            int: Код завершения выполнения.
        """


        threads = []

        def _threaded_execution(host: str, task_name: str, task: dict, username: str, password: str):
            if task_name.lower() == "reboot":
                # Для задачи "reboot" для одиночного хоста вызываем reboot_vm,
                # передавая signal_get и ready_signal
                reboot.reboot_vm(
                    host, vm_dates, username, password,
                    signal_get=task.get('signal get'),
                    ready_signal=task.get('signal set')
                )
            else:
                ssh_command.cmd(
                    host=host,
                    command=task['command'],
                    username=username,
                    password=password,
                    vm_dates=vm_dates,
                    signal_set=task.get('signal set'),
                    signal_get=task.get('signal get'),
                    task_name=task_name
                )

        # Итерация по командам
        for target, tasks in commands.items():
            if target.startswith("g_"):
                # Команды для группы ВМ
                group_name = target[2:]
                if vms_groups and group_name in vms_groups:
                    for task_name, task in tasks.items():
                        if task_name.lower() == "reboot":
                            # Для задачи reboot для группы вызываем reboot_group,
                            # передавая signal_get и ready_signal
                            def group_worker():
                                reboot.reboot_group(
                                    vms_groups[group_name], vm_dates, username, password,
                                    signal_get=task.get('signal get'),
                                    ready_signal=task.get('signal set')
                                )
                            thread = threading.Thread(target=group_worker)
                            threads.append(thread)
                            thread.start()
                        else:
                            for host in vms_groups[group_name]:
                                thread = threading.Thread(
                                    target=_threaded_execution,
                                    args=(host, task_name, task, username, password)
                                )
                                threads.append(thread)
                                thread.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                # Команды для отдельного хоста
                host = target
                for task_name, task in tasks.items():
                    if task_name.lower() == "reboot":
                        # Для одиночного хоста с задачей "reboot" вызываем reboot_vm,
                        # передавая signal_get и ready_signal
                        thread = threading.Thread(
                            target=lambda: reboot.reboot_vm(
                                host, vm_dates, username, password,
                                signal_get=task.get('signal get'),
                                ready_signal=task.get('signal set')
                            )
                        )
                        threads.append(thread)
                        thread.start()
                    else:
                        thread = threading.Thread(
                            target=_threaded_execution,
                            args=(host, task_name, task, username, password)
                        )
                        threads.append(thread)
                        thread.start()

        for thread in threads:
            thread.join()

        # Удаление всех сигналов после выполнения команд
        signals.remove_all()
        return 0
    
    @classmethod
    def scp(cls, scp_settings: dict, vm_dates: dict, groups: dict = None,
            username: str = "u", password: str = "1") -> int:
        """
        Выполняет копирование файлов между локальной системой и виртуальными машинами.

        Args:
            scp_settings (dict): Настройки для копирования файлов.
                scp_settings = {
                    'mode' = '' # pull/push получение или отправка файла
                    'path_host' = '' # путь на хосте
                    'path_vm' = '' # путь на ВМ
                }
            vm_dates (dict): Полная информация о виртуальных машинах.
                vms_date (list): Полная информация о виртуальных машинах.
                    vm_dates = {'hostname':{
                        'host-port':'*',
                        'ip':'10.0.0.11', #  ip внутренней сети
                        'sshnum':'',
                        'ip_bridge':'*.*.*.*', # ip моста
                        'cpus':'*',
                        'memory':'*', # RAM
                        'disk':'*'}
                        }    
            groups (dict, optional): Группы виртуальных машин.
                vms_groups = {
                    'databases': ['db1', 'db2'],
                }
            username (str, optional): Имя пользователя для SSH. По умолчанию "u".
            password (str, optional): Пароль для SSH. По умолчанию "1".

        Returns:
            int: Код завершения выполнения.
        """

        scp_command.execute(
            scp=scp_settings,
            vms_date=vm_dates,
            groups=groups,
            username=username,
            password=password
        )
        return 0

    @classmethod
    def set_hosts(cls, domain: str, vms_dates: dict,
                  username: str = "u", password: str = "1"):
        """
        Настраивает файл /etc/hosts на всех указанных виртуальных машинах.

        Args:
            domain (str): Домен для формирования FQDN.
            vm_dates (dict): Полная информация о виртуальных машинах.
                vms_date (list): Полная информация о виртуальных машинах.
                    vm_dates = {'hostname':{
                        'host-port':'*',
                        'ip':'10.0.0.11', #  ip внутренней сети
                        'sshnum':'',
                        'ip_bridge':'*.*.*.*', # ip моста
                        'cpus':'*',
                        'memory':'*', # RAM
                        'disk':'*'}
                        }  
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".
        """

        set_hosts.set_hosts(
            domain=domain,
            vms_dates=vms_dates,
            username=username,
            password=password
        )
        return 0

    @classmethod
    def sed(cls, sed_conf: dict, vm_dates: dict, groups: dict = None,
            username: str = "u", password: str = "1"):
        """
        Выполняет замену строки в файле

        Args:
            sed_conf (dict): Настройки для копирования файлов.
                sed_conf = {
                           'suac': {
                               'path': '/etc/postgresql/15/main/pg_hba.conf',
                               'old':'# IPv4 local connections:',
                               'new':'' 
                           },
                           'g_database': { ... }  # если ключ начинается с "g_", то команда выполнится для группы
                         }
            vm_dates (dict): Полная информация о виртуальных машинах.
                vms_date (list): Полная информация о виртуальных машинах.
                    vm_dates = {'hostname':{
                        'host-port':'*',
                        'ip':'10.0.0.11', #  ip внутренней сети
                        'sshnum':'',
                        'ip_bridge':'*.*.*.*', # ip моста
                        'cpus':'*',
                        'memory':'*', # RAM
                        'disk':'*'}
                        }    
            groups (dict, optional): Группы виртуальных машин.
                vms_groups = {
                    'databases': ['db1', 'db2'],
                }
            username (str, optional): Имя пользователя для SSH. По умолчанию "u".
            password (str, optional): Пароль для SSH. По умолчанию "1".

        Returns:
            int: Код завершения выполнения.
        """

        sed.sed(
            sed_conf=sed_conf,
            vms_dates=vm_dates,
            groups=groups,
            username=username,
            password=password
        )
        return 0

    @classmethod    
    def freeipa(cls, domain,vm_dates: dict, groups: dict = None,
            username: str = "u", password: str = "1"):
        """_summary_

        Args:
            domain (dict): Настройка для freeipa 
                domain = {
                    'settings': {
                        'domain': 'example.com',
                        'admin_password': 'secret'
                    },
                    'domain': {
                        'host': 'domain'
                    },
                    'client': {
                        'host': 'database'  # если значение начинается с "g_", то это группа хостов
                    }
                }
            vm_dates (dict): Полная информация о виртуальных машинах   
                vm_dates = {'hostname':{
                    'host-port':'*',
                    'ip':'10.0.0.11', #  ip внутренней сети
                    'sshnum':'',
                    'ip_bridge':'*.*.*.*', # ip моста
                    'cpus':'*',
                    'memory':'*', # RAM
                    'disk':'*'}
                    }    
            groups (dict, optional): Группы виртуальных машин.
                vms_groups = {
                    'databases': ['db1', 'db2'],
                }
            username (str, optional): Имя пользователя для SSH. По умолчанию "u".
            password (str, optional): Пароль для SSH. По умолчанию "1".

        Returns:
            _type_: _description_
        """
        freeipa.freeipa(domain = domain, vm_dates=vm_dates, vms_groups=groups, ssh_user=username, ssh_password=password)
        return 0

    apt: _AptManagerProtocol = cast(_AptManagerProtocol, _apt._AptManager())

