import os
import requests
import json
from ..._system_command.SystemCommands import SystemCommands as system_commands

class _VirtInstall():    
    """
    Класс для работы с Vagrant.

    Основные функции:
    - Генерация Vagrantfile с настройками для ВМ.
    - Добавление образа (бокса) и запуск ВМ.
    - Определение соответствия версии ОС и доступного бокса.

    Этот класс позволяет автоматизировать процесс создания и настройки ВМ с использованием Vagrant.
    """

    def __init__(self, box: str, rc: str, vms_date: dict):
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

    @staticmethod
    def _box_wrapper(self) -> tuple:
        """
        Определяет соответствие версии операционной системы и доступного бокса.

        Returns:
            tuple: Имя и URL бокса.
        """
        box = self.box
        astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-box-config'
        response_ac = requests.get(astra_config_url)
        if response_ac.status_code == 200:
            with open('box-config.json', 'wb') as acb:
                acb.write(response_ac.content)
        else:
            print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

        with open('box-config.json', 'r') as r:
            dates = json.loads(r.read())

        true_key = False
        box_name = ''
        box_url = ''
        for i in dates['libvirt_box']:
            if box in str(i):
                for key in i.keys():
                    if str(key).endswith('s'):
                        true_key = key
                        box_name = f"{i['1.8.1s'][0]}.qcow2"
                        box_url = i[true_key][1]
        if not true_key:
            for i in dates['libvirt_box']:
                if str(box).startswith('1.7'):
                    if '1.7.5.s' in str(i):
                        box_name = f"{i['1.7.5.s'][0]}.qcow2"
                        box_url = i['1.7.5.s'][1]
                        os_version = 'alse17'                        

                elif str(box).startswith('1.8'):
                    if '1.8.1s' in str(i):
                        box_name = f"{i['1.8.1.s'][0]}.qcow2"
                        box_url = i['1.8.1s'][1]
                        os_version = 'alse18'
        return box_name, box_url, os_version
    


    @staticmethod
    def build(self):
        box_name, box_url, os_version = self._box_wrapper(self)
        system_commands.cmd(f'sudo apt-get install wget')        
        system_commands.cmd(f'wget {box_url}')
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
        # Путь к файлу
        network_path = "network.xml"

        # Запись содержимого в файл
        with open(network_path, "w") as file:
            file.write(xml_content)

        system_commands.check_output_command(f"virsh --connect qemu:///system net-create {network_path}")   # Создание сети        
        for hostname, info in self.vm_dates.items():
            print(f"Хост: {hostname}")

            cpu = info['cpu']
            ram = info['ram']     
            system_commands.check_output_command(f"cp {box_name} {hostname}.qcow2")
            system_commands.check_output_command(f"virt-install --connect qemu:///system -n {hostname} \
                                                 --memory {ram} --vcpus {cpu} \
                                                 --import --disk path=./{box_name} --os-variant {os_version} \
                                                 --network network=test")
            
            # Получаем IP-адрес
            ip = system_commands.check_output_command(
                f"virsh -c qemu:///system domifaddr {hostname} | awk '{{print $4}}' | tail -n 2"
                ).strip().split('/')[0]  # Убираем лишние пробелы/переводы строк

            # Записываем полученный IP обратно в словарь
            self.vm_dates[hostname]['ip_bridge'] = ip

            print(f"Получен IP для {hostname}: {ip}")
        return 0