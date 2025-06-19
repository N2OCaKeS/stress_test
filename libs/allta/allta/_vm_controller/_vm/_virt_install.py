
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep, time

import requests

from ..._system_command.SystemCommands import SystemCommands as system_commands
from .._libs._scp_command import _SCP_Command
from .._libs._ssh_command import _SSH_Command
from .._base_commands._reboot._reboot import _Reboot 



class _VirtInstall:
    """
    Класс для работы с Vagrant.

    Основные функции:
    - Генерация Vagrantfile с настройками для ВМ.
    - Добавление образа (бокса) и запуск ВМ.
    - Определение соответствия версии ОС и доступного бокса.

    Этот класс позволяет автоматизировать процесс создания и настройки ВМ с использованием Vagrant.
    """


    def __init__(self, box: str, rc: str, vms_date: dict, kernel: str):

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
            kernel (str, optional): То какое ядро необходимо установить (полный вывод uname -r), если не задано то по умолчанию установит то же что и на хосте
        """
        self.box = box
        self.rc = rc
        self.vms_date = vms_date
        self.kernel = kernel


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
                    os_version = 'alse17'
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
                    return box['1.8.1.o'][0], box['1.8.1.o'][1], 'alse17' # В версии 1.7 отсутсвует alse18 из за чего ВМ на 1.8 не собираеются сейчас на 1.7 все отрабатывает штатно при использовании alse17


    @staticmethod
    def _build_vm(hostname, info, box, os_version, system_commands, vm_path):
        t_start = time()
        
        try:
            cpu = info['cpu']
            ram = info['ram']
            disk = f"{hostname}.qcow2"
            print(system_commands.check_output_command(f"cp {vm_path}/{box}.qcow2 {vm_path}/{disk}"))
            print(system_commands.check_output_command(f"chmod 777 {vm_path}/{disk}"))
            sleep(10)
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
        network_path = f"{vm_path}/network.xml"
        with open(network_path, "w") as file:
            file.write(xml_content)

        try:
            system_commands.check_output_command(
                f"virsh --connect qemu:///system net-create {network_path} && "
                f"virsh --connect qemu:///system net-autostart --network test"
            )
        except Exception as e:
            print("WARNING: Сеть test возможно уже существует.")

        print("\n==> Создание ВМ параллельно с задержкой 10 сек...")
        start_ts = time()

        with ThreadPoolExecutor() as executor:
            futures = []
            # проходим по всем ВМ с индексом
            for idx, (hostname, info) in enumerate(self.vms_date.items()):
                delay = idx * 10  # 0, 10, 20, ...
                
                # заворачиваем вызов _build_vm в задачу, которая заснётся перед стартом
                def scheduled_build(h=hostname, 
                                     i=info, 
                                     d=delay):
                    # ждём нужное время от момента запуска первой задачи
                    to_wait = d - (time() - start_ts)
                    if to_wait > 0:
                        sleep(to_wait)
                    return self._build_vm(
                        h, i, self.box, os_version, system_commands, vm_path
                    )

                futures.append(executor.submit(scheduled_build))

            # собираем результаты как обычно
            results = {}
            for fut in as_completed(futures):
                host = fut.result()
                if host:
                    print(f"[{host}] ВМ создана и запущена")
                    results[host] = True
                else:
                    print("[FAIL] Не удалось создать ВМ")
                    results[host] = False

        print("Ждём запуска всех ВМ (90 сек)...")
        sleep(90)
        print("\n==> Получение IP адресов...")

        with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
            ip_futures = [
                executor.submit(self._get_ip, hostname, system_commands)
                for hostname in self.vms_date.keys()
            ]
            for future in as_completed(ip_futures):
                hostname, ip = future.result()
                self.vms_date[hostname]['ip_bridge'] = ip

        print("\n==> Скачиваем releases.json...")
        if self.rc:
            # 1. Скачиваем releases.json
            resp = requests.get('http://allta.devos.astralinux.ru/rest/api/get-repo-path')
            resp.raise_for_status()
            releases = resp.json()
            print("\n==> Парсим releases.json...")
            # 2. Берём нужный список deb-строк по self.rc
            try:
                sources_lines = releases[self.rc]
            except KeyError:
                raise ValueError(f"Нет записи для релиза '{self.rc}' в releases.json")

            # Собираем их в одну строку с разделителем \n
            sources_str = "\\n".join(sources_lines)

            # 3. Узнаём текущее локальное ядро (если нужно передавать в скрипт)
            print("\n==> Парсим ядро...")
            if self.kernel is not None:
                kernel = self.kernel
            else:
                kernel = system_commands.check_output_command("uname -r").strip()
            
            if "-generic" in kernel:
                suffix = "generic"
            elif "-lowlatency" in kernel:
                suffix = "lowlatency"
            else:
                raise ValueError(f"Неизвестный тип ядра: {kernel}")

            # Извлекаем major.minor версию (первые два числа)
            version_parts = kernel.split("-")[0].split(".")
            if len(version_parts) < 2:
                raise ValueError(f"Некорректный формат версии: {kernel}")
            major_minor = ".".join(version_parts[:2])  # "5.10"
            apt_kernel = f"linux-{major_minor}-{suffix}"
            print(f"\n==> Получили ядро: {kernel}, apt_kernel {apt_kernel}...")

            def start_prepare(cmd_template = None, reboot = None):
                class SafeDict(dict):
                    def __missing__(self, key):
                        # если ключа нет — возвращаем его же в фигурных скобках
                        return '{' + key + '}'

                with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
                    futures = []
                    for host in self.vms_date:
                        if reboot is None:
                            # собираем словарь с теми ключами, которые реально подставляем
                            mapping = SafeDict(
                                host=host,
                                sources_str=sources_str,
                                apt_kernel=apt_kernel,
                                kernel=kernel
                            )
                            # и делаем безопасный .format_map()
                            full_cmd = cmd_template.format_map(mapping)

                            futures.append(
                                executor.submit(
                                    _SSH_Command.cmd,
                                    host,
                                    full_cmd,
                                    self.vms_date,
                                    "u",
                                    "1",
                                    task_name = 'prepare'
                                )
                            )
                        if reboot == 1:
                            futures.append(
                                executor.submit(
                                    _Reboot.reboot_vm,
                                    host,
                                    self.vms_date,
                                    "u",
                                    "1"
                                )
                            )

                for future in as_completed(futures):
                    result = future.result()
                    if reboot is None:
                        # Обработка результата команды SSH (словарь)
                        h = result.get("host", "unknown")
                        if result.get("status") == "ok":
                            print(f"[{h}] prepare успешно выполнен")
                        else:
                            print(f"[{h}] prepare ошибка: {result.get('output')}")
                    elif reboot == 1:
                        # Обработка результата перезагрузки (булево значение)
                        h = host  # используем текущий host из внешнего контекста
                        if result is True:
                            print(f"[{h}] VM успешно перезагружена")
                        else:
                            print(f"[{h}] Ошибка при перезагрузке VM")

                    
                    
            cmds = [
                # 1) hostname и /etc/hosts
                "sudo hostnamectl set-hostname {host} && "
                "echo -e '127.0.0.1\tlocalhost\n127.0.0.1\t{host}\t10.177.103.10\tallta.devos.astralinux.ru\tallta\n10.177.43.1\treleases.devos.astralinux.ru\ttreleases' | sudo tee /etc/hosts",

                # 2) репо
                "echo -e '{sources_str}' | sudo tee /etc/apt/sources.list",

                # 3) обновление и Astra Update
                "sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive astra-update -A -T -r",

                # 4) зависимости
                "sudo DEBIAN_FRONTEND=noninteractive apt-get install rsync htop gcc make perl -y",

                # 5) установка ядра
                "sudo DEBIAN_FRONTEND=noninteractive apt-get install {apt_kernel} -y",

                # 6) поиск нужного menuentry_id в grub (awk)
                """kernel_conf=$(sudo grep menuentry_id /boot/grub/grub.cfg | awk '{print $17}' | grep "{kernel}" | tr -d "'")""",

                # 7) добавляем GRUB_DEFAULT=0, если не задан
                "if ! grep -q '^GRUB_DEFAULT=' /etc/default/grub; then "
                "echo 'GRUB_DEFAULT=0' | sudo tee -a /etc/default/grub; fi",

                # 8) перезаписываем GRUB_DEFAULT
                "sudo sed -i \"s|GRUB_DEFAULT=.*|GRUB_DEFAULT=${kernel_conf}|\" /etc/default/grub",

                # 9) обновляем и проверяем grub
                "sudo update-grub",
                "grep '^GRUB_DEFAULT=' /etc/default/grub",
            ]

            print(f"\n\n\nСтавим hostname\n\n\n")
            start_prepare(cmds[0])
            print(f"\n\n\nСтавим репозиторий\n\n\n")
            start_prepare(cmds[1])
            print(f"\n\n\nAtra Update\n\n\n")
            start_prepare(cmds[2])
            print(f"\n\n\nСтавим зависимости\n\n\n")
            start_prepare(cmds[3])   
            print(f"\n\n\nСтавим ядро\n\n\n")
            start_prepare(cmds[4])
            print(f"\n\n\nОбновляем grub\n\n\n")
            start_prepare(cmds[5])
            start_prepare(cmds[6])
            start_prepare(cmds[7])
            start_prepare(cmds[8])   
            print(f"\n\nПерезагружаем ВМ\n\n\n")
            start_prepare(reboot=1)
        return self.vms_date

