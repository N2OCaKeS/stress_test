from ._libs._system_commands import _system_commands as system_commands
from ._libs._ssh_comand import _ssh_command as ssh_command
from ._libs._scp_comand import _scp_command as scp_command

# from ._base_commands._apt._apt import _apt_manager as apt_manager
from ._base_commands._apt._apt_prorocol import _AptManagerProtocol
from ._base_commands._apt import _apt 

from ._base_commands._set_hosts._set_hosts_protocol import _HostsManagerProtocol
from ._base_commands._set_hosts import _HostsManager



from ._vm._vagrant import _Vagrant 
from ._vm._virtual_machine import _VirtualMashines
from ._vm._vbox_manage import _Vbox_manager as vbox_manager

from os import system
from typing import cast

import threading


class VBox(_VirtualMashines):
    @classmethod
    def prepare(cls, path_prepare) -> int:
        """
        Прекондишн

        Args:
            path_prepare (str): путь до прекондишна
        """
        return system.cmd_with_returncode(f"sudo bash {path_prepare}")    
    
    @classmethod
    def build(cls, path_to_vagrantfile: str, box: str, rc: str, vms: list, vms_date: list, provision_script: str) -> int: 
        """
        Сборка VM

        Args:
            path_to_vagrantfile (str): путь куда сохранить и откуда будет запущен Vagrantfile 
                path_to_vagrantfile = './vagrant'
            box (str): имя образа
            rc (str): версия ос
            vms (list): список имен ВМ
            vm_dates (dict): полная информация о ВМ пример:
                vm_dates = {'hostname':{'host-port':'*',
                    'ip':'10.0.0.11',
                    'sshnum':'',
                    'ip_bridge':'*.*.*.*',
                    'cpus':'*',
                    'memory':'*',
                    'disk':'*'},
                    } 
            provision_script (str): путь до provision.sh Vagrant
                provision_script = ./vagrant/provision/provision.sh
        """

        vagrant = _Vagrant(path_to_vagrantfile, box, rc, vms_date)

        vagrant.vagrant_up(provision_script)

        vbox_manager.set_bridge_network(vms)
        vbox_manager.create_snapshots_all_vm(vms)
        system_commands.cmd('vboxmanage natnetwork list')
        system_commands.cmd('vboxmanage list hostonlyifs')
        system_commands.cmd('vboxmanage list bridgedifs')
        system_commands.cmd('vboxmanage list vms')

        #TODO Узнать нужен ли этот блок

        # if path.isfile('/home/iface/iface'):
        #     with open('/home/iface/iface', 'r') as r:
        #         if_name = r.read().strip()
        # else:
        #     if rc.startswith('1.7'):
        #         if_name = 'eth0'  #  Узнать правильные названия интерфейсов и указать их
        #     elif rc.startswith('1.8'):
        #         if_name = 'ens5'   
        return 0

    @classmethod
    def check(cls, vms: list, vm_dates: dict):
        """
        Проверка доступности ВМ через ping

        Args:
            vms (list): список имен ВМ
            vm_dates (dict): полная информация о ВМ пример:
                vm_dates = {'hostname':{'host-port':'*',
                    'ip':'10.0.0.11',
                    'sshnum':'',
                    'ip_bridge':'*.*.*.*',
                    'cpus':'*',
                    'memory':'*'}, 
                    ...
                    }
        """
        def _check_ping():
            bad_vms = [vm for vm in vms if system.cmd_with_returncode(
                f"ping -c 1 {vm_dates[vm]['ip_bridge']}") != 0]
            return bad_vms

        if _check_ping():
            print("Не удалось решить проблемы с настройкой сети, ВМ недоступна(ы)")
            return 1
        else:
            print("All vms is available")
            return 0        
    
    @classmethod
    def execute(cls, vm_dates: dict, commands: dict, vms_groups: dict = None, username: str = "u", password: str = "1") -> int:
        """
        Выполнение команд

        Args:
            vms_groups (dict): словарь с группами хостов пример:
                groups = {
                    'group_name': ['host1', 'host2'],
                    ...
                    }

            vm_dates (dict): полная информация о ВМ пример:
                vm_dates = {'hostname':{'host-port':'*',
                    'ip':'10.0.0.11',
                    'sshnum':'',
                    'ip_bridge':'*.*.*.*',
                    'cpus':'*',
                    'memory':'*'}, 
                    ...
                    }

            commands (dict): команды для выполнения на ВМ пример:
                commands = {
                    'hostname': { # имя хоста или имя группы хостов на которых нужно выполнить команду имя группы будет называться с g_ в начале
                        'task_name': { # имя задачи
                            'command':'yes 1 | adduser user0',  # Command to execute
                            'signal set': 'User created',       # Signal to set
                            'signal get': ''                    # Signal to get
                        }    
                    }, 
                    ...
                    }

            username (str): имя пользователя для подключения к ВМ
            password (str): пароль для подключения к ВМ
        """
        
        def _threaded_execution(host: str, task_name: str, task: dict, username: str, password: str):
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

        threads = []

        # Итерация по ключам в словаре commands
        for target, tasks in commands.items():
            if target.startswith("g_"):
                # Если ключ начинается с "g_", это команды для группы хостов
                group_name = target[2:]  # убираем префикс "g_"
                if vms_groups and group_name in vms_groups:
                    for host in vms_groups[group_name]:
                        for task_name, task in tasks.items():
                            thread = threading.Thread(target=_threaded_execution, args=(host, task_name, task))
                            threads.append(thread)
                            thread.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                # Если ключ не начинается с "g_", это имя конкретного хоста
                host = target
                for task_name, task in tasks.items():
                    thread = threading.Thread(target=_threaded_execution, args=(host, task_name, task))
                    threads.append(thread)
                    thread.start()

        for thread in threads:
            thread.join()

        return 0

    @classmethod
    def scp(cls, scp_settings: dict, vm_dates: dict, groups: dict = None,
            username: str = "u", password: str = "1") -> int:
        """
        Выполняет копирование файлов между локальной системой и виртуальными машинами
        с использованием настроек из словаря scp_settings. Для копирования файлов используется
        класс _scp_command, который запускает копирование для каждого хоста в отдельном потоке.
        
        Args:
            scp_settings (dict): Словарь с настройками для копирования файлов.
                Формат:
                {
                    'hostname_or_group': {
                        'mode': 'push' или 'pull',
                        'path_host': '/путь/на/локальной/системе',
                        'path_vm': '/путь/на/виртуальной/машине'
                    },
                    ...
                }
            vm_dates (dict): Словарь с информацией о виртуальных машинах.
            groups (dict, optional): Словарь групп виртуальных машин для массового копирования.
            username (str, optional): Имя пользователя для SSH-подключения (по умолчанию "u").
            password (str, optional): Пароль для SSH-подключения (по умолчанию "1").
        
        Returns:
            int: 0 при успешном выполнении копирования файлов, иначе возвращается 1.
        """


        scp_command.execute(
            scp=scp_settings,
            vms_date=vm_dates,
            groups=groups,
            username=username,
            password=password
        )

    apt: _AptManagerProtocol = cast(_AptManagerProtocol, _apt._apt_manager())
    hosts: _HostsManagerProtocol = cast(_HostsManagerProtocol, _HostsManager())



    