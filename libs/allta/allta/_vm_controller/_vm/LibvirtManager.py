from ..._decorators.Decorators import BaseDecorators
from ..._system_command.SystemCommands import SystemCommands as system_commands

from time import sleep

class LibvirtManager():
    """
    Класс для управления виртуальными машинами через libvirt.

    Основные функции:
    - Включение и выключение ВМ.
    - Создание снимков всех настроенных ВМ.
    - Настройка сетевого интерфейса ВМ на мостовой режим с использованием nmcli.
    - Получение списка доступных ВМ.
    """

    @BaseDecorators.trycorator
    @staticmethod 
    def power_on(vms):
        """
        Включает одну или несколько виртуальных машин.

        Args:
            vms (str or list): Имя одной виртуальной машины (str) или список имён виртуальных машин (list).

        Returns:
            int: Код завершения выполнения.
        """
        if isinstance(vms, str):
            vms = [vms]
        
        for vm in vms:
            system_commands.cmd(f'virsh --connect qemu:///system start {vm}')
        return 0

    @BaseDecorators.trycorator
    @staticmethod 
    def power_off(vms):
        """
        Выключает одну или несколько виртуальных машин.

        Args:
            vms (str or list): Имя одной виртуальной машины (str) или список имён виртуальных машин (list).

        Returns:
            int: Код завершения выполнения.
        """
        if isinstance(vms, str):
            vms = [vms]
        
        for vm in vms:
            system_commands.cmd(f'virsh --connect qemu:///system shutdown {vm}')
        return 0
    

    
    @BaseDecorators.trycorator
    @staticmethod 
    def create_snapshot(vms, snapshot_name=None):
        """
        Создаёт снимки всех указанных виртуальных машин.

        Args:
            vms (list): Список имён виртуальных машин.
            snapshot_name (str): Имя снимка.
        """
        if snapshot_name:
            for vm in vms:
                system_commands.cmd(f'virsh --connect qemu:///system snapshot-create-as --domain {vm} --name "{snapshot_name}"')            
        else:
            for vm in vms:
                system_commands.cmd(f'virsh --connect qemu:///system snapshot-create-as --domain {vm} --name "snapshot_1"')
        return 0




    # @BaseDecorators.trycorator
    # @staticmethod    
    # def get_ip_network(vms_date: dict):
    #     """
    #     Настраивает сетевой интерфейс виртуальных машин на мостовой режим с использованием network.d.

    #     Args:
    #         vms (list): Список имён виртуальных машин.
    #         bridge_name (str): Имя мостового интерфейса (по умолчанию 'virbr0').

    #     Returns:
    #         int: Код завершения выполнения.
    #     """
    #     # Проверяем существование моста с помощью nmcli
    #     return 0

    @BaseDecorators.trycorator
    @staticmethod   
    def check_vm_list():
        """
        Возвращает список всех виртуальных машин.

        Returns:
            str: Список виртуальных машин.
        """
        return system_commands.check_output_command('virsh --connect qemu:///system list --all')
    