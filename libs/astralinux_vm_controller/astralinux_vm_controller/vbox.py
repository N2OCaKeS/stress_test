from libs._virtual_machine import _VirtualMashines
from libs._vagrant import _Vagrant 
from libs._system_commands import _system_commands as system_commands
from libs._ssh_comand import _ssh_command as ssh_command
from libs._scp_comand import _scp_command as scp_command

from base_commands._apt import _apt_manager as apt_manager
from base_commands._set_hosts import _set_hosts as set_hosts

from vbox_manage._vbox_manage import _Vbox_manager as vbox_manager

from os import system

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
    def apt(cls) -> int: 
        """
        Заглушка для метода apt, переопределение происходит через вложенный класс AptManager.
        Реальная логика работы с пакетами будет использовать методы install и remove.
        """
        pass

    # Переопределяем apt на уровне класса,
    # чтобы можно было обращаться напрямую к методам install и remove:
    apt = apt_manager()

    def scp(cls) -> int:
        pass

    scp = scp_command()

    def hosts(cls) -> int:
        pass

    hosts = set_hosts()