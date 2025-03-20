from ..._decorators.Decorators import BaseDecorators
from ..._system_command import _System_Commands as system_commands

from time import sleep

class _Vboxmanager():
    """
    Класс для управления виртуальными машинами в VirtualBox.

    Основные функции:
    - Включение и выключение ВМ.
    - Создание снимков всех настроенных ВМ.
    - Настройка сетевого интерфейса ВМ на мостовой режим.
    - Получение списка доступных ВМ.

    Этот класс использует команды VirtualBox для управления ВМ.
    """

    @BaseDecorators.trycorator
    @staticmethod 
    def poweron_vms(vms):
        """
        Включает одну или несколько виртуальных машин.

        Args:
            vms (str or list): Имя одной виртуальной машины (str) или список имён виртуальных машин (list).

        Returns:
            int: Код завершения выполнения.
        """
        # Преобразуем строку в список, если передана только одна ВМ
        if isinstance(vms, str):
            vms = [vms]
        
        # Включаем каждую ВМ
        for vm in vms:
            system_commands.cmd(f'vboxmanage startvm {vm} --type headless')
        return 0

    

    @BaseDecorators.trycorator
    @staticmethod 
    def poweroff_vms(vms):
        """
        Выключает одну или несколько виртуальных машин.

        Args:
            vms (str or list): Имя одной виртуальной машины (str) или список имён виртуальных машин (list).

        Returns:
            int: Код завершения выполнения.
        """
        # Преобразуем строку в список, если передана только одна ВМ
        if isinstance(vms, str):
            vms = [vms]
        
        # Выключаем каждую ВМ
        for vm in vms:
            system_commands.cmd(f'vboxmanage controlvm {vm} poweroff')
        return 0
    


    @BaseDecorators.trycorator
    @staticmethod 
    def create_snapshots_all_vm(vms):
        """
        Создаёт снимки всех указанных виртуальных машин.

        Args:
            vms (list): Список имён виртуальных машин.

        Returns:
            None
        """
        for vm in vms:
            system_commands.cmd(f'vboxmanage snapshot "{vm}" take "snapshot_1"')


    @BaseDecorators.trycorator
    @staticmethod    
    def set_bridge_network(vms: list):
        """
        Настраивает сетевой интерфейс виртуальных машин на мостовой режим.

        Args:
            vms (list): Список имён виртуальных машин.

        Returns:
            int: Код завершения выполнения.
        """
        adapter_name = system_commands.check_output_command(
            "vboxmanage list bridgedifs | grep Name | awk '{print$2}' | head -n 1")
        print(f'Bridge interface found as: {adapter_name}')

        for vm in vms:
            try:
                num_interface = '1'
                system_commands.cmd(f'vboxmanage controlvm {vm} poweroff')
                sleep(1)
                system_commands.cmd(
                    f'vboxmanage modifyvm {vm} --nic{num_interface} bridged')
                system_commands.cmd(
                    f'vboxmanage modifyvm {vm} --bridgeadapter{num_interface} {adapter_name}')
                system_commands.cmd(f'vboxmanage startvm {vm} --type headless')
            except Exception as e:
                print(f'Type:{str(type(e).__name__)},\nError: {str(e)}')
            return 1
    

    @BaseDecorators.trycorator
    @staticmethod   
    def _check_vm_list():
        """
        Возвращает список доступных виртуальных машин.

        Returns:
            str: Список виртуальных машин.
        """
        return system_commands.check_output_command('vboxmanage list vms')









