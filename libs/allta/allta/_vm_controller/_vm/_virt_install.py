import os
from time import sleep, time
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
        Находит бокс по точному совпадению ключа self.box.
        Если не найден — возвращает дефолт для 1.7 и 1.8.
        """
        # Получаем файл
        system_commands.cmd('rm -rf test-box-config.json')
        system_commands.cmd(f'wget ftp://10.177.103.10/boxes/test-box-config.json')
        with open('test-box-config.json', 'r') as r:
            dates = json.load(r)

        box_name = ''
        box_url = ''
        os_version = ''

        # 1. Поиск по точному совпадению в libvirt_box
        for box in dates['libvirt_box']:
            if self.box in box:
                box_name = box[self.box][0]
                box_url = box[self.box][1]
                # Определяем ОС по ключу
                if '1.7' in self.box:
                    os_version = 'alse17'
                elif '1.8' in self.box:
                    os_version = 'alse18'
                return box_name, box_url, os_version

        # 2. Если не найден — дефолты
        if self.box.startswith('1.7'):
            # Дефолт для 1.7
            for box in dates['libvirt_box']:
                if '1.7.5.o' in box:
                    return box['1.7.5.o'][0], box['1.7.5.o'][1], 'alse17'
        elif self.box.startswith('1.8'):
            # Дефолт для 1.8
            for box in dates['libvirt_box']:
                if '1.8.1.o' in box:
                    return box['1.8.1.o'][0], box['1.8.1.o'][1], 'alse18'


    @staticmethod
    def _build_vm(hostname, info, box, os_version, system_commands, vm_path):
        t_start = time()
        
        try:
            cpu = info['cpu']
            ram = info['ram']
            disk = f"{hostname}.qcow2"
            system_commands.check_output_command(f"cp {vm_path}/{box}.qcow2 {vm_path}/{disk}")
            system_commands.check_output_command(f"chmod 777 {vm_path}/{disk}")
            print (system_commands.check_output_command(
                f"virt-install --connect qemu:///system -n {hostname} "
                f"--memory {ram} --vcpus {cpu} --import --disk path={vm_path}/{disk} "
                f"--os-variant {os_version} --network network=test "
                "--noautoconsole --noreboot --cpu host-model,+vmx --autostart"
            ))
            # virt-install --connect qemu:///system -n test --memory 6144 --vcpus 6  --import --disk path=/tmp/1.7.5.o.qcow2 --os-variant alse17 --network network=test --noautoconsole --noreboot --cpu host-model,+vmx
            sleep(10)
            print (system_commands.check_output_command(f"virsh --connect qemu:///system start {hostname}"))
            print(f"[{hostname}] DONE {round(time()-t_start, 1)} сек")
            return hostname
        except Exception as e:
            print(f"[{hostname}] ERROR: {e}")
            return None

    @staticmethod
    def _get_ip(hostname, system_commands):
        sleep(10)  # Даём время на получение DHCP
        try:
            ip_output = system_commands.check_output_command(
                f"virsh -c qemu:///system domifaddr {hostname} | awk '{{print $4}}' | tail -n 2"
            ).strip()
            ip = ip_output.split('/')[0] if ip_output else None
            print(f"[{hostname}] IP: {ip}")
            return hostname, ip
        except Exception as e:
            print(f"[{hostname}] IP ERROR: {e}")
            return hostname, None

    def build(self):
        vm_path = '/vms'
        box_name, box_url, os_version = self._box_wrapper()
        system_commands.cmd(f'mkdir {vm_path} && chmod 777 {vm_path}')        
        system_commands.cmd(f'virsh pool-define-as vms dir --target {vm_path} && virsh pool-build vms && virsh pool-start vms && virsh pool-autostart vms')
        system_commands.cmd(f'rm -rf {vm_path}/{box_name}.tar.gz; wget -P {vm_path} {box_url}')
        system_commands.cmd(f'tar xzf {vm_path}/{box_name}.tar.gz -C {vm_path}/')                
        system_commands.cmd(f"mv {vm_path}/{box_name}.qcow2 {vm_path}/{self.box}.qcow2")


        # Сеть libvirt (создать если ещё нет)
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
        try:
            system_commands.check_output_command(
                f"virsh --connect qemu:///system net-create {network_path} && "
                f"virsh --connect qemu:///system net-autostart --network test"
            )
        except Exception as e:
            print("WARNING: Сеть test возможно уже существует.")

        print("\n==> Создание ВМ параллельно...")
        with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
            futures = [
                executor.submit(
                    self._build_vm, hostname, info, self.box, os_version, system_commands, vm_path
                )
                for hostname, info in self.vms_date.items()
            ]
            results = {}
            for future in as_completed(futures):
                hostname = future.result()
                if hostname:
                    print(f"[{hostname}] ВМ создана и запущена")
                    results[hostname] = True
                else:
                    print("[FAIL] Не удалось создать ВМ")
                    results[hostname] = False

        print("Ждём запуска всех ВМ (60 сек)...")
        sleep(60)
        print("\n==> Получение IP адресов параллельно...")
        with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
            ip_futures = [
                executor.submit(self._get_ip, hostname, system_commands)
                for hostname in self.vms_date.keys()
            ]
            for future in as_completed(ip_futures):
                hostname, ip = future.result()
                self.vms_date[hostname]['ip_bridge'] = ip
        return self.vms_date

