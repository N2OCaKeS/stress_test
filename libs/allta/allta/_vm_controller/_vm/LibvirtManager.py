from ..._decorators.Decorators import BaseDecorators
from ..._system_command.SystemCommands import SystemCommands as system_commands
from .._libs._scp_command import _SCP_Command
from .._libs import _scripts as scripts_pkg

from time import sleep
from importlib.resources import files, as_file
class LibvirtManager():
    """
    Класс для управления виртуальными машинами через libvirt.

    Основные функции:
    - Включение и выключение ВМ.
    - Создание снимков всех настроенных ВМ.
    - Настройка сетевого интерфейса ВМ на мостовой режим с использованием nmcli.
    - Получение списка доступных ВМ.
    """

    class Vm():
        """
        Класс для управления виртуальными машинами через Libvirt.

        Основные функции:
        - Включение и выключение ВМ.
        - Получение списка доступных ВМ.
        """        
        @BaseDecorators.trycorator
        @staticmethod   
        def vm_list():
            """
            Возвращает список всех виртуальных машин.

            Returns:
                str: Список виртуальных машин.
            """
            return system_commands.check_output_command('virsh --connect qemu:///system list --all')

        @BaseDecorators.trycorator
        @staticmethod 
        def start(vms):
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
        def stop(vms):
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
                system_commands.cmd(f'virsh --connect qemu:///system destroy {vm}')
            return 0     
        @BaseDecorators.trycorator
        @staticmethod 
        def bridge(vms_date: dict, new_vms_date: dict, username: str = "u", password: str = "1"):
            """Устанавливает тип соединения bridge на ВМ

            Args:
                vms_dates (dict): Полная информация о виртуальных машинах.
                new_vms_dates (dict): Информация о виртуальных машинах, только с учетом новых ip для bridge
                username (str, optional): Имя пользователя для SSH. По умолчанию "u".
                password (str, optional): Пароль для SSH. По умолчанию "1".
            """
            from ..Libvit import Libvirt

            net = system_commands.check_output_command("ip -4 route get 8.8.8.8 | awk '{for(i=1;i<=NF;i++){if($i==\"dev\") d=$(i+1); if($i==\"src\") s=$(i+1)}} END{print s, d}'")
            system_commands.check_output_command("sudo cp /etc/network/interfaces /etc/network/interfaces.bak || true")
            ip, phy_if = net.strip().split()
            bridge = "br0"
            cfg = f"""sudo tee /etc/network/interfaces > /dev/null <<EOF
auto lo
iface lo inet loopback

auto {bridge}
iface {bridge} inet static
    address {ip}
    netmask 255.255.255.0
    gateway 10.177.103.254
    dns-nameservers 10.177.128.198 10.177.180.246 10.177.181.142
    bridge_ports {phy_if}
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0

iface {phy_if} inet manual
EOF
"""
            system_commands.cmd_with_returncode(cfg)
            system_commands.cmd_with_returncode(f"sudo ifdown {phy_if} || true && sudo ifup {bridge} && sudo systemctl restart networking")
            print ("Net well done")
            def get_file_path(filename: str) -> str:
                res = files(scripts_pkg).joinpath(filename)
                with as_file(res) as p:
                    return str(p)
            vms_list = list(vms_date.keys())
            network_path = get_file_path("network.sh")
            print(network_path)
            provision_path = get_file_path("provision.sh")
            print(provision_path)
            group = {'all': vms_list}
            scp_prepare = {
                "g_all": [
                    {
                        'mode': 'push',
                        'path_host': f"{provision_path}",
                        'path_vm': '/home/u/env_provision.sh'
                    }
                ],
            }
            _SCP_Command.execute(scp=scp_prepare, vms_date=vms_date, groups=group, username=username, password=password)
            prepare = {}
            for vm_name in vms_date:
                prepare[vm_name] = {
                    'prepare': {
                        'command': (
                            f"sudo chmod 777 /home/u/env_provision.sh && "
                            f"sudo su -c '/home/u/env_provision.sh {new_vms_date[vm_name]['ip_bridge']}' && "
                            f"sudo rm /home/u/env_provision.sh"
                        ),
                        'signal set': 'prepare',
                        'signal get': ""
                    },
                    'conf': {
                        'command': "(sleep 2 && sudo shutdown -r now) &",                        
                        'signal set': '',
                        'signal get': ['prepare']
                    }
                }

            print(prepare)

            Libvirt.execute(commands=prepare, vms_dates=vms_date, vms_groups=group, username=username, password=password)
            print("execute succes")
            for vm in vms_list:
                commands = f"{network_path} {vm}"
                print(commands)
                system_commands.cmd_with_returncode(f"{network_path} {vm}")
            return 0

    class Snapshot():
        """
        Класс для управления снимками виртуальных машин через Libvirt.

        Основные функции:
        - Создать снимок
        - Удалить снимок
        - Откатить снимок
        """
        @BaseDecorators.trycorator
        @staticmethod 
        def create(vms, snapshot_name=None):
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
                    system_commands.cmd(f'virsh --connect qemu:///system snapshot-create-as --domain {vm} --name "snapshot"')
            return 0
        
        @BaseDecorators.trycorator
        @staticmethod
        def delete(vms, snapshot_name):
            """
            Удаляет указанный снимок у всех указанных виртуальных машин.

            Args:
                vms (list): Список имён виртуальных машин.
                snapshot_name (str): Имя снимка.
            """
            for vm in vms:
                system_commands.cmd(f'virsh --connect qemu:///system snapshot-delete --domain {vm} --snapshotname "{snapshot_name}"')
        
        @BaseDecorators.trycorator
        @staticmethod
        def revert(vms, snapshot_name):
            """
            Удаляет указанный снимок у всех указанных виртуальных машин.

            Args:
                vms (list): Список имён виртуальных машин.
                snapshot_name (str): Имя снимка.
            """
            for vm in vms:
                system_commands.cmd(f'virsh --connect qemu:///system snapshot-revert --domain {vm} --snapshotname "{snapshot_name}"')    


    