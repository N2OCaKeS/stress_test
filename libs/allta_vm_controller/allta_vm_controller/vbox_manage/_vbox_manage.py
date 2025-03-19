from astralinux_decorators import trycorator as trycorator
from ..libs._system_commands import _system_commands as system_command

from time import sleep

class _Vbox_manager():


    @trycorator.trycorator
    @staticmethod 
    def poweron_vms(vms):
        """
        Включает одну или несколько ВМ.

        Args:
            vms (str or list): имя одной ВМ (str) или список имён ВМ (list).
        """
        # Преобразуем строку в список, если передана только одна ВМ
        if isinstance(vms, str):
            vms = [vms]
        
        # Включаем каждую ВМ
        for vm in vms:
            system_command.cmd(f'vboxmanage startvm {vm} --type headless')
        return 0

    

    @trycorator.trycorator
    @staticmethod 
    def poweroff_vms(vms):
        """
        Выключает одну или несколько ВМ.

        Args:
            vms (str or list): имя одной ВМ (str) или список имён ВМ (list).
        """
        # Преобразуем строку в список, если передана только одна ВМ
        if isinstance(vms, str):
            vms = [vms]
        
        # Выключаем каждую ВМ
        for vm in vms:
            system_command.cmd(f'vboxmanage controlvm {vm} poweroff')
        return 0
    


    @trycorator.trycorator
    @staticmethod 
    def create_snapshots_all_vm(vms):
        """
        Создает снимки всех созданных и настроенных ВМ

        Args:
            vms (list): список имен ВМ
        """
        for vm in vms:
            system_command.cmd(f'vboxmanage snapshot "{vm}" take "snapshot_1"')


    @trycorator.trycorator
    @staticmethod    
    def set_bridge_network(vms: list):
        """ 
        Устанавливает тип сети на мост

        Args:
            vms (list): список ВМ
        """
        adapter_name = system_command.check_output_command(
            "vboxmanage list bridgedifs | grep Name | awk '{print$2}' | head -n 1")
        print(f'Bridge interface found as: {adapter_name}')

        for vm in vms:
            try:
                num_interface = '1'
                system_command.cmd(f'vboxmanage controlvm {vm} poweroff')
                sleep(1)
                system_command.cmd(
                    f'vboxmanage modifyvm {vm} --nic{num_interface} bridged')
                system_command.cmd(
                    f'vboxmanage modifyvm {vm} --bridgeadapter{num_interface} {adapter_name}')
                system_command.cmd(f'vboxmanage startvm {vm} --type headless')
            except Exception as e:
                print(f'Type:{str(type(e).__name__)},\nError: {str(e)}')
            return 1
    

    @trycorator.trycorator
    @staticmethod   
    def _check_vm_list():
        """
        Показывает список доступных ВМ
        """
        return system_command.check_output_command('vboxmanage list vms')
    


        
    



    
