import subprocess
import os


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


class VBox():
    """
    Класс подготоваливает окружение под провайдер Virtualbox,   
    разворачивает и производит настройку необходимых ВМ.
    """
    @classmethod
    def prepare(cls) -> int:
        return system.cmd_with_returncode("sudo bash oom/oom_prepare_vbox.sh")

    @classmethod
    def build(cls, box_name: str, box_url: str, kernel: str, rc: str) -> int:
        def _check_vm_list():
            return system.check_output_command('vboxmanage list vms')
                
        if rc.startswith('1.7'):
            pg_version = 11
            if_name = 'eth0'
        elif rc.startswith('1.8'):
            pg_version = 15
            if_name = 'ens5'

        
        system.cmd('apt install -fy')

        #Отключаем модули kvm, чтобы небыло конфликта ресурсов с vbox
        try:
            modules = system.check_output_command("lsmod | grep kvm | awk '{print$1}'").split('\n')
            print(f'KVM modules found: {modules}')
            [system.cmd(f'sudo modprobe -r {module}') for module in modules if modules]
            print(system.cmd('lsmod | grep kvm'))
            system.cmd('lsmod | grep kvm')
        except Exception as e:
            print(f'Error is: {type(e).__name__}\nMessage is: {str(e)}')
        
        if system.cmd_with_returncode(f'cd oom && vagrant box add {box_name} {box_url} --force') != 0:
            return 1
        if system.cmd_with_returncode(f'cd oom && VAGRANT_EXPERIMENTAL="disks" UPDATE={box_name} BOX_URL={box_url} RC={rc} KL={kernel} vagrant up --provider=virtualbox') != 0:
            return 1
    
        return 0
    
    @classmethod
    def check(cls) -> int:
        found_values = ['oom', 'крах']

        if os.path.isfile('/home/tests/results'):
            with open('/home/tests/results', 'r') as r:
                text = r.read()

            found = False
            for value in found_values:
                if value in text:
                    if 'bloom' in text and value == 'oom':
                        continue  
                    
                    found = True
                    break
            if found:
                print('Провалено. Обнаружены ошибки: ООМ, Крах СУБД')
                return 1
            else:
                print('Тест пройден, ошибки не обнаружены')
                return 0
        else: 
            print('Файл </home/tests/results> не найден')
            return 1
                