import VirtualMashines
import libs.vagrant as vagrant

from vbox_manage import vbox_manage
from commands.apt import AptManager

from os import system


class vbox(VirtualMashines):
    @classmethod
    def prepare(cls, path_prepare) -> int:
        """
        Прекондишн

        Args:
            path_prepare (str): путь до прекондишна
        """
        return system.cmd_with_returncode(f"sudo bash {path_prepare}")    
    
    @classmethod
    def build(cls, box: str, rc: str, vms: list, vms_date: list) -> int:
        """
        Сборка VM

        Args:
            box (str): имя образа
            rc (str): версия ос
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
        vagrant.vagrant(box, rc ,vms)
        vbox_manage.set_bridge_network(vms)
        vbox_manage.create_snapshots_all_vm
        system.cmd('vboxmanage natnetwork list')
        system.cmd('vboxmanage list hostonlyifs')
        system.cmd('vboxmanage list bridgedifs')
        system.cmd('vboxmanage list vms')

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
    def execute(cls, vms: list, vms_groups: dict,vm_dates: dict, commands: list) -> int: # TODO НЕ реализовано
        """
        Выполнение команд

        Args:
            vms (list): список имен ВМ
            vms_groups (dict): словарь с группами хостов пример:
                hosts = {
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
                    'hostname': {
                        'command',
                        'set/get signal',
                        'signal name'
                    }, ...}

        """

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
    apt = AptManager()