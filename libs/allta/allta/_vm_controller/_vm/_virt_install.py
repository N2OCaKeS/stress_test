import os
from time import sleep
import requests
import json
from ..._system_command.SystemCommands import SystemCommands as system_commands
from concurrent.futures import ThreadPoolExecutor, as_completed

class _VirtInstall:
    """
    Класс для работы с Vagrant.

    Основные функции:
    - Генерация Vagrantfile с настройками для ВМ.
    - Добавление образа (бокса) и запуск ВМ.
    - Определение соответствия версии ОС и доступного бокса.

    Этот класс позволяет автоматизировать процесс создания и настройки ВМ с использованием Vagrant.
    """

    def __init__(self, box: str, rc: str, vms_date: dict, prepare: str = None):
        """
        Класс для работы с Vagrant

        Args:
            path_to_vagrantfile (str): путь до папки, где будет сгенерирован Vagrantfile
            box (str): версия используемого бокса
            rc (str): версия ОС
            vms_date (dict): словарь с информацией о ВМ, например:
                {
                    "database1": { "ip": "10.177.103.111", "cpus": "4", "memory": "32768", "disk": "40960" },
                    "database2": { "ip": "10.177.103.112", "cpus": "4", "memory": "32768" },
                    ...
                }
            Из имени ВМ будут формироваться поля :name, :hostname и :args.
            Если ключ disk присутствует, то для ВМ будет создан дополнительный диск указанного размера.
        """
        self.box = box
        self.rc = rc
        self.vms_date = vms_date

    def _box_wrapper(self) -> tuple:
        """
        Определяет соответствие версии операционной системы и доступного бокса.

        Returns:
            tuple: Имя и URL бокса, версия ОС.
        """
        # astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-box-config'
        # response_ac = requests.get(astra_config_url)
        # if response_ac.status_code == 200:
        #     with open('box-config.json', 'wb') as acb:
        #         acb.write(response_ac.content)
        # else:
        #     print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

        system_commands.cmd(f'wget ftp://10.177.103.10/boxes/test-box-config.json')        
        with open('test-box-config.json', 'r') as r:
            dates = json.loads(r.read())

        true_key = False
        box_name = ''
        box_url = ''
        os_version = ''
        for i in dates['libvirt_box']:
            if self.box in str(i):
                for key in i.keys():
                    if str(key).endswith('s'):
                        true_key = key
                        box_name = f"{i[true_key][0]}"
                        box_url = i[true_key][1]
                        # Версия ОС определяется по ключу
                    if str(self.box).startswith('1.7'):
                        os_version = 'alse17'
                    elif str(self.box).startswith('1.8'):
                        os_version = 'alse18'
        if not true_key:
            for i in dates['libvirt_box']:
                if str(self.box).startswith('1.7'):
                    if '1.7.5.s' in str(i):
                        box_name = f"{i['1.7.5.s'][0]}"
                        box_url = i['1.7.5.s'][1]
                    os_version = 'alse17'
                elif str(self.box).startswith('1.8'):
                    if '1.8.1s' in str(i):
                        box_name = f"{i['1.8.1s'][0]}"
                        box_url = i['1.8.1s'][1]
                    os_version = 'alse18'
        return box_name, box_url, os_version
    
    @staticmethod
    def _build_vm(hostname, info, box, os_version, system_commands):
        cpu = info['cpu']
        ram = info['ram']
        disk = f"{hostname}.qcow2"
        system_commands.check_output_command(f"cp {box}.qcow2 {disk}")
        system_commands.check_output_command(
            f"virt-install --connect qemu:///system -n {hostname} "
            f"--memory {ram} --vcpus {cpu} --import --disk path=./{disk} "
            f"--os-variant {os_version} --network network=test "
            "--noautoconsole --noreboot --cpu host-model,+vmx"
        )
        system_commands.check_output_command(f"virsh --connect qemu:///system start {hostname}")
        return hostname
    @staticmethod
    def _get_ip(hostname, system_commands):
        # Даем немного времени, чтобы DHCP успел выдать адрес
        sleep(10)
        ip = system_commands.check_output_command(
            f"virsh -c qemu:///system domifaddr {hostname} | awk '{{print $4}}' | tail -n 2"
        ).strip().split('/')[0]
        return hostname, ip


    def build(self):
        box_name, box_url, os_version = self._box_wrapper()
        # system_commands.cmd(f'wget {box_url}')
        # system_commands.cmd(f'tar xzf {box_name}.tar.gz')                
        system_commands.cmd(f"mv {box_name}.qcow2 {self.box}.qcow2")
        xml_content = """<network>
    <name>test</name>
    <forward mode="nat"/>
    <domain name="test"/>
    <ip address="192.168.100.1" netmask="255.255.255.0">
        <dhcp>
        <range start="192.168.100.128" end="192.168.100.254"/>
        </dhcp>
    </ip>
    </network>"""
        network_path = "network.xml"
        with open(network_path, "w") as file:
            file.write(xml_content)
        system_commands.check_output_command(
            f"virsh --connect qemu:///system net-create {network_path} && "
            f"virsh --connect qemu:///system net-autostart --network test"
        )

        # 1. Создаём все ВМ параллельно
        with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
            futures = [
                executor.submit(self._build_vm, hostname, info, self.box, os_version, system_commands)
                for hostname, info in self.vms_date.items()
            ]
            for future in as_completed(futures):
                hostname = future.result()
                print(f"ВМ {hostname} создана и запущена.")
        sleep(60)
        # 2. Получаем IP-адреса после запуска всех ВМ
        with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
            ip_futures = [
                executor.submit(self._get_ip, hostname, system_commands)
                for hostname in self.vms_date.keys()
            ]
            for future in as_completed(ip_futures):
                hostname, ip = future.result()
                self.vms_date[hostname]['ip_bridge'] = ip
                print(f"Получен IP для {hostname}: {ip}")

        return self.vms_date    
