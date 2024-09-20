from abc import ABC, abstractmethod
import subprocess
import os
from time import sleep



class system:
    """
    Класс для обращения к системе
    """
    @staticmethod
    def check_output_command(command: str) -> str:
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors

    @staticmethod
    def cmd_with_returncode(command: str) -> int:
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        return subprocess.run(command, shell=True)



class bl:
    """
    Класс с внутренними методами, которые относятся   
    к тесту с балансировщиком.
    """
    @staticmethod
    def box_wrapper(box: str, dates: dict) -> tuple:
        """
        Метод определяет соответствие версий ОС и доступности
        бокса, возвращает соответствующий url и name
        """
        true_key = False
        box_name = ''
        box_url = ''
        for i in dates['vagrant_box']:
            if box in str(i):
                for key in i.keys():
                    if str(key).endswith('s'):
                        true_key = key
                        box_name = i[true_key][0]
                        box_url = i[true_key][1]             
                
        if true_key == False:
            for i in dates['vagrant_box']:
                if str(box).startswith('1.7'):
                    if '1.7.1.s' in str(i):
                        box_name = i['1.7.1.s'][0]
                        box_url = i['1.7.1.s'][1]
                elif str(box).startswith('1.8'):
                    if '1.8.0.s' in str(i):
                        box_name = i['1.8.0.s'][0]
                        box_url = i['1.8.0.s'][1]
        
        return box_name, box_url
    
    @staticmethod
    def check(method, uzs, *args, **kwargs):
        """
        Проверка успешности выполнения переданного метода
        """
        result = method(*args, **kwargs)
        if result != 0:
            uzs.upload_test_cycle_status(zefir_status='fail')
            print(f"Method \"{method.__name__}\" return bad result. Execution stopped, result: {result}")
            exit(1)



class VirtualMashines(ABC):
    """
    Абстрактный конвейер\n
    prepare: Подготовка окружения   
    build: Развертывание и настройка ВМ   
    check: Проверка доступности   
    execute: Запуск плэйбуков
    """
    @abstractmethod
    def prepare(cls) -> int:
        pass

    @abstractmethod
    def build(cls, box_name: str, box_url: str, kernel: str, rc: str, vms: list) -> int:
        pass

    @abstractmethod
    def check(cls, vms: list, vm_dates: dict) -> int:
        pass

    @abstractmethod
    def execute(cls, vms: list, ansible_commands: list, vm_dates: dict) -> int:
        pass



class VBox(VirtualMashines):
    """
    Класс подготоваливает окружение под провайдер Virtualbox,   
    разворачивает и производит настройку необходимых ВМ,   
    проверяет их доступность, запускает плэйбуки.
    """
    @classmethod
    def prepare(cls) -> int:
        return system.cmd_with_returncode("sudo bash balance/bl_prepare_vbox.sh")
    
    @classmethod
    def build(cls, box_name: str, box_url: str, kernel: str, rc: str, vms: list) -> int:
        def _set_bridge_network(vm, adapter_name):
            try:
                num_interface = '1'
                system.cmd(f'vboxmanage controlvm {vm} poweroff'); sleep(1)
                system.cmd(f'vboxmanage modifyvm {vm} --nic{num_interface} bridged')
                system.cmd(f'vboxmanage modifyvm {vm} --bridgeadapter{num_interface} {adapter_name}')
                system.cmd(f'vboxmanage startvm {vm} --type headless')
            except Exception as e:
                print(f'Type:{str(type(e).__name__)},\nError: {str(e)}')

        def _check_vm_list():
            return system.check_output_command('vboxmanage list vms')

        system.cmd('apt install -fy')
        if system.cmd_with_returncode(f'cd balance && vagrant box add {box_name} {box_url} --force') != 0:
            return 1
        if system.cmd_with_returncode(f'cd balance && UPDATE={box_name} BOX_URL={box_url} KERNEL={kernel} RC={rc} vagrant up --provider=virtualbox') != 0:
            return 1
        bridge_iface = system.check_output_command("vboxmanage list bridgedifs | grep Name | awk '{print$2}' | head -n 1")
        print(f'Bridge interface found as: {bridge_iface}')
        [_set_bridge_network(vm, bridge_iface) for vm in vms if vm in _check_vm_list()]
        [system.cmd(f'vboxmanage snapshot "{vm}" take "snapshot_1"') for vm in vms if vm in _check_vm_list()]
        system.cmd('vboxmanage natnetwork list')
        system.cmd('vboxmanage list hostonlyifs')
        system.cmd('vboxmanage list bridgedifs')
        system.cmd('vboxmanage list vms')
        return 0
    
    @classmethod
    def check(cls, vms: list, vm_dates: dict):
        def _check_ping():
            bad_vms = [vm for vm in vms if system.cmd_with_returncode(f"ping -c 1 {vm_dates[vm]['ip_bridge']}") != 0]
            return bad_vms
        
        if _check_ping():   
            print("Не удалось решить проблемы с настройкой сети, ВМ недоступна(ы)")
            return 1
        else: 
            print("All vms is available")
            return 0
        
    @classmethod
    def execute(cls, vms: list, ansible_commands: list, vm_dates: dict) -> int:
        no_fprint = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
        vm_creds = {name:f'sshpass -v -p 1 ssh {no_fprint} u@{vm_dates[name]["ip_bridge"]}' for name in vms}
        print('\n***--------- Connecting credentials ---------***\n')
        for key, value in vm_creds.items():
            print(key, value)

        while attempts_count < 1:
            for command in ansible_commands:
                print(f"Begin task: {command}")
                result_code = system.cmd_with_returncode(command)
                print(f'\nResult code: {result_code}\n')
                if command == ansible_commands[-1] and result_code == 0:
                    print("Ansible commands cycle is fully executed")
                if result_code != 0:
                    print(f"\nResult code: {result_code}\n")
                    print("Command execution failed, stopping the loop!")
                    attempts_count += 1
                    break
            else:
                attempts_count += 1
                break

        print(f'Attempts count was: {attempts_count}')
        return 0



class LVirt(VirtualMashines):
    @classmethod
    def prepare(cls) -> int:
        return system.cmd_with_returncode("sudo bash balance/bl_prepare_lvirt.sh")
    
