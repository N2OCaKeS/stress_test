import libs.decorators as decorators

from os import system
from time import sleep

class vbox_manage(): 
    @decorators.trycorator    
    def set_bridge_network(vms: list):
        """ 
        Устанавливает тип сети на мост

        Args:
            vms (list): список ВМ
        """
        adapter_name = system.check_output_command(
            "vboxmanage list bridgedifs | grep Name | awk '{print$2}' | head -n 1")
        print(f'Bridge interface found as: {adapter_name}')

        for vm in vms:
            try:
                num_interface = '1'
                system.cmd(f'vboxmanage controlvm {vm} poweroff')
                sleep(1)
                system.cmd(
                    f'vboxmanage modifyvm {vm} --nic{num_interface} bridged')
                system.cmd(
                    f'vboxmanage modifyvm {vm} --bridgeadapter{num_interface} {adapter_name}')
                system.cmd(f'vboxmanage startvm {vm} --type headless')
            except Exception as e:
                print(f'Type:{str(type(e).__name__)},\nError: {str(e)}')
            return 1
    
    @decorators.trycorator   
    def _check_vm_list():
        """
        Показывает список доступных ВМ
        """
        return system.check_output_command('vboxmanage list vms')
    
    @decorators.trycorator   
    def create_snapshots_all_vm(vms):
        """
        Создает снимки всех созданных и настроенных ВМ

        Args:
            vms (list): список имен ВМ
        """
        for vm in vms:
            system.cmd(f'vboxmanage snapshot "{vm}" take "snapshot_1"')

    
