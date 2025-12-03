import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep, time
import copy
import requests

from importlib.resources import files, as_file
from .._libs import _scripts as scripts_pkg

from ..._system_command.SystemCommands import SystemCommands as system_commands
from .._libs._scp_command import _SCP_Command
from .._libs._ssh_command import _SSH_Command
from .._base_commands._reboot._reboot import _Reboot
from .LibvirtManager import LibvirtManager
from typing import Optional


class _VirtInstall:
    """
    Класс для работы с Vagrant.

    Основные функции:
    - Генерация Vagrantfile с настройками для ВМ.
    - Добавление образа (бокса) и запуск ВМ.
    - Определение соответствия версии ОС и доступного бокса.

    Этот класс позволяет автоматизировать процесс создания и настройки ВМ с использованием Vagrant.
    """

    def __init__(self, box: str, rc: str, vms_date: dict, kernel: Optional[str] = None):
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
            kernel (str, optional): То какое ядро необходимо установить (полный вывод uname -r), если не задано то оставит ядро по умолчанию 
        """
        self.box = box
        self.rc = rc
        self.vms_date = copy.deepcopy(vms_date)
        self.original_vms_date = copy.deepcopy(vms_date)
        self.vm_path = "/vms"
        self.kernel = kernel

    def _box_wrapper(self) -> tuple[str, str, str]:
        """
        Находит бокс по точному совпадению ключа self.box.
        Если не найден — возвращает дефолт для 1.7 и 1.8.
        """
        # Получаем файл
        system_commands.cmd("rm -rf test-box-config.json")
        system_commands.cmd("wget ftp://10.177.103.10/boxes/test-box-config.json")
        with open("test-box-config.json", "r") as r:
            dates = json.load(r)

        box_name = ""
        box_url = ""
        os_version = ""

        # 1. Поиск по точному совпадению в libvirt_box
        for box in dates["libvirt_box"]:
            if self.box in box:
                box_name = box[self.box][0]
                box_url = box[self.box][1]
                # Определяем ОС по ключу
                if "1.7" in self.box:
                    os_version = "alse17"
                elif "1.8" in self.box:
                    os_version = "alse17"
                elif "debian" in self.box:
                    os_version = "debian12"
                return box_name, box_url, os_version

        # 2. Если не найден — дефолты
        if self.box.startswith("1.7"):
            # Дефолт для 1.7
            for box in dates["libvirt_box"]:
                if "1.7.5.o" in box:
                    return box["1.7.5.o"][0], box["1.7.5.o"][1], "alse17"
        elif self.box.startswith("1.8"):
            # Дефолт для 1.8
            for box in dates["libvirt_box"]:
                if "1.8.1.o" in box:
                    return (
                        box["1.8.1.o"][0],
                        box["1.8.1.o"][1],
                        "alse17",
                    )  # В версии 1.7 отсутсвует alse18 из за чего ВМ на 1.8 не собираеются сейчас на 1.7 все отрабатывает штатно при использовании alse17

        # Если бокс не найден — явно завершаем выполнение
        raise ValueError(f"Не удалось определить бокс для '{self.box}'")

    @staticmethod
    def _build_vm(hostname, info, box, os_version, system_commands, vm_path):
        t_start = time()

        try:
            cpu = info["cpu"]
            ram = info["ram"]
            disk = f"{hostname}.qcow2"
            print(
                system_commands.check_output_command(
                    f"cp {vm_path}/{box}.qcow2 {vm_path}/{disk}"
                )
            )
            print(system_commands.check_output_command(f"chmod 777 {vm_path}/{disk}"))
            sleep(10)
            print(
                system_commands.check_output_command(
                    f"virt-install --connect qemu:///system -n {hostname} "
                    f"--memory {ram} --vcpus {cpu} --import --disk path={vm_path}/{disk} "
                    f"--os-variant {os_version} --network network=test "
                    "--noautoconsole --noreboot --cpu host-model,+vmx --autostart"
                    # "--controller type=pci,model=pcie-root,index=0 "
                    # "--controller type=pci,model=pcie-root-port,index=1 "
                    # "--controller type=pci,model=pcie-root-port,index=2 "
                    # "--controller type=pci,model=pcie-root-port,index=3 "
                    # "--controller type=pci,model=pcie-root-port,index=4 "
                    # "--controller type=pci,model=pcie-root-port,index=5 "
                    # "--controller type=pci,model=pcie-root-port,index=6 "
                )
            )
            # virt-install --connect qemu:///system -n test --memory 6144 --vcpus 6 --import --disk path=/var/lib/libvirt/images/pool/test.qcow2 --os-variant alse17 --network network=test --noautoconsole --noreboot --cpu host-model,+vmx --controller type=pci,model=pcie-root,index=0 --controller type=pci,model=pcie-root-port,index=1

            sleep(10)
            print(
                system_commands.check_output_command(
                    f"virsh --connect qemu:///system start {hostname}"
                )
            )
            print(f"[{hostname}] DONE {round(time()-t_start, 1)} сек")
            return hostname
        except Exception as e:
            print(f"[{hostname}] ERROR: {e}")
            return None

    @staticmethod
    def _get_ip(hostname, system_commands):
        sleep(10)
        try:
            ip_output = system_commands.check_output_command(
                f"virsh -c qemu:///system domifaddr {hostname} | awk '{{print $4}}' | tail -n 2"
            ).strip()
            ip = ip_output.split("/")[0] if ip_output else None
            print(f"[{hostname}] IP: {ip}")
            return hostname, ip
        except Exception as e:
            print(f"[{hostname}] IP ERROR: {e}")
            return hostname, None

    def _set_ip_bridge(self, vms_date):
        LibvirtManager.Vm.bridge(
            vms_date=self.vms_date, new_vms_date=vms_date, username="u", password="1"
        )

    def _resize_disk(self, vms_date: dict, username: str = "u", password: str = "1"):
        from ..Libvit import Libvirt

        for vm in vms_date:
            LibvirtManager.Vm.stop(vm)
            system_commands.cmd_with_returncode(
                f"qemu-img resize '{self.vm_path}'/{vm}.qcow2 {vms_date[vm]["disk"]}G"
            )
            LibvirtManager.Vm.start(vm)
        sleep(90)

        vms_list = list(vms_date.keys())
        group = {"all": vms_list}

        def get_file_path(filename: str) -> str:
            res = files(scripts_pkg).joinpath(filename)
            with as_file(res) as p:
                return str(p)

        resize_script = get_file_path("resize_disk.sh")

        scp_prepare = {
            "g_all": [
                {
                    "mode": "push",
                    "path_host": f"{resize_script}",
                    "path_vm": "/home/u/resize_disk.sh",
                }
            ],
        }
        _SCP_Command.execute(
            scp=scp_prepare,
            vms_date=self.vms_date,
            groups=group,
            username=username,
            password=password,
        )
        prepare = {}
        for vm_name in vms_date:
            prepare[vm_name] = {
                "prepare: resize disk": {
                    "command": (
                        "sudo chmod 777 /home/u/resize_disk.sh && "
                        "sudo su -c '/home/u/resize_disk.sh' && "
                        "sudo rm /home/u/resize_disk.sh"
                    ),
                    "signal set": "resize_disk",
                    "signal get": "",
                },
                "prepare: resize disk confirm": {
                    "command": "(sleep 2 && sudo shutdown -r now) &",
                    "signal set": "",
                    "signal get": ["resize_disk"],
                },
            }
        Libvirt.execute(
            commands=prepare,
            vms_dates=self.vms_date,
            vms_groups=group,
            username=username,
            password=password,
        )
        sleep(60)

    def build(self, bridge: bool = False):
        vm_path = self.vm_path
        box_name, box_url, os_version = self._box_wrapper()
        system_commands.cmd(f"sudo mkdir -p {vm_path} && sudo chmod 777 {vm_path}")
        system_commands.cmd(
            f"virsh --connect qemu:///system pool-define-as vms dir --target {vm_path} && virsh --connect qemu:///system pool-build vms && virsh --connect qemu:///system pool-start vms && virsh --connect qemu:///system pool-autostart vms"
        )
        system_commands.cmd(
            f"rm -rf {vm_path}/{box_name}.tar.gz; wget -P {vm_path} {box_url}"
        )
        system_commands.cmd(f"tar xzf {vm_path}/{box_name}.tar.gz -C {vm_path}/")

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
            print(f"WARNING: Сеть test возможно уже существует. Ошибка: {e}")

        print("\n==> Создание ВМ параллельно с задержкой 10 сек...")
        start_ts = time()

        with ThreadPoolExecutor() as executor:
            futures = []
            # проходим по всем ВМ с индексом
            for idx, (hostname, info) in enumerate(self.vms_date.items()):
                delay = idx * 10  # 0, 10, 20, ...

                # заворачиваем вызов _build_vm в задачу, которая заснётся перед стартом
                def scheduled_build(h=hostname, i=info, d=delay):
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
                self.vms_date[hostname]["ip_bridge"] = ip
        system_commands.cmd(
            f"rm {vm_path}/{self.box}.qcow2 {vm_path}/{self.box}.tar.gz"
        )

        if self.box != "vm_station":
            print("\n==> Скачиваем releases.json...")
            if self.rc:
                # 1. Скачиваем releases.json
                resp = requests.get(
                    "http://allta.devos.astralinux.ru/rest/api/get-repo-path"
                )
                resp.raise_for_status()
                releases = resp.json()
                print("\n==> Парсим releases.json...")
                # 2. Берём нужный список deb-строк по self.rc
                try:
                    sources_lines = releases[self.rc]
                except KeyError:
                    raise ValueError(
                        f"Нет записи для релиза '{self.rc}' в releases.json"
                    )

                # Собираем их в одну строку с разделителем \n
                sources_str = "\\n".join(sources_lines)

                print("\n==> Парсим ядро...")
                if self.kernel:
                    kernel = self.kernel
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

            def start_prepare(cmd_template: Optional[str] = None, reboot: Optional[int] = None):
                class SafeDict(dict):
                    def __missing__(self, key):
                        # если ключа нет — возвращаем его же в фигурных скобках
                        return "{" + key + "}"

                with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
                    futures = []
                    for host in self.vms_date:
                        if reboot is None:
                            if cmd_template is None:
                                raise ValueError("cmd_template должен быть задан при запуске без перезагрузки")
                            if self.kernel:
                                # собираем словарь с теми ключами, которые реально подставляем
                                mapping = SafeDict(
                                    host=host,
                                    sources_str=sources_str,
                                    apt_kernel=apt_kernel,
                                    kernel=kernel,
                                )
                            else:
                                mapping = SafeDict(
                                    host=host,
                                    sources_str=sources_str,
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
                                    task_name="prepare",
                                )
                            )
                        if reboot == 1:
                            futures.append(
                                executor.submit(
                                    _Reboot.reboot_vm,
                                    host,
                                    self.vms_date,
                                    "u",
                                    "1",
                                    sleep=180,
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

            cmds = [
                # 1) hostname и /etc/hosts
                "sudo hostnamectl set-hostname {host} && sudo timedatectl set-ntp true && "
                "echo -e '127.0.0.1\tlocalhost\n127.0.0.1\t{host}\n10.177.103.10\tallta.devos.astralinux.ru\tallta\n10.177.43.1\treleases.devos.astralinux.ru\treleases' | sudo tee /etc/hosts",
                # 2) репо
                "echo -e '{sources_str}' | sudo tee /etc/apt/sources.list",
                # 3) обновление и Astra Update
                "sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive astra-update -A -T -r",
                # 4) зависимости
                "sudo DEBIAN_FRONTEND=noninteractive apt-get install rsync htop gcc make perl qemu-guest-agent -y",
                # 5) установка ядра
                "sudo DEBIAN_FRONTEND=noninteractive apt-get install {apt_kernel} -y",
                # 6) поиск нужного menuentry_id в grub (awk)
                """kernel_conf=$(sudo grep menuentry_id /boot/grub/grub.cfg | awk '{print $17}' | grep "{kernel}" | tr -d "'")""",
                # 7) добавляем GRUB_DEFAULT=0, если не задан
                "if ! grep -q '^GRUB_DEFAULT=' /etc/default/grub; then "
                "echo 'GRUB_DEFAULT=0' | sudo tee -a /etc/default/grub; fi",
                # 8) перезаписываем GRUB_DEFAULT
                'sudo sed -i "s|GRUB_DEFAULT=.*|GRUB_DEFAULT=${kernel_conf}|" /etc/default/grub',
                # 9) обновляем и проверяем grub
                "sudo update-grub",
                "grep '^GRUB_DEFAULT=' /etc/default/grub",
            ]

            if self.box == "vm_station":
                version = ["1.7.5.9", "1.8.1.6"]
                for vers in version:
                    print(
                        f"\n\n\n\033[31mНастраиваем ВМ для версии ОС: {vers}\033[0m\n\n\n"
                    )
                    vms_list = list(self.vms_date.keys())
                    for vm in vms_list:
                        disk = f"{vm}.qcow2"
                        system_commands.cmd_with_returncode(
                            f"virsh --connect qemu:///system destroy {vm}"
                        )
                        revert_snap = (
                            f"sudo qemu-img snapshot -a {vers} {vm_path}/{disk}"
                        )
                        system_commands.cmd_with_returncode(revert_snap)
                        system_commands.cmd_with_returncode(
                            f"virsh --connect qemu:///system start {vm}"
                        )
                    sleep(60)
                    print("\n\n\nСтавим hostname\n\n\n")
                    start_prepare(cmds[0])
                    print("\n\n\nAtra Update\n\n\n")
                    start_prepare(cmds[2])
                    print("\n\n\nСтавим зависимости\n\n\n")
                    start_prepare(cmds[3])
                    print("\n\nПерезагружаем ВМ\n\n\n")
                    start_prepare(reboot=1)
                    for vm in vms_list:
                        disk = f"{vm}.qcow2"
                        system_commands.cmd(
                            f'virsh --connect qemu:///system snapshot-create-as --domain {vm} --name "{vers}_build"'
                        )
            elif self.box == "debian12":
                cmds = [
                    "sudo hostnamectl set-hostname {host} && sudo timedatectl set-ntp true && "
                    "echo -e '127.0.0.1\tlocalhost\n127.0.0.1\t{host}\n10.177.103.10\tallta.devos.astralinux.ru\tallta\n10.177.43.1\treleases.devos.astralinux.ru\treleases' | sudo tee /etc/hosts",
                    "sudo DEBIAN_FRONTEND=noninteractive apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install wget curl rsync htop gcc make perl qemu-guest-agent -y",
                ]
                print("\n\n\nСтавим hostname\n\n\n")
                start_prepare(cmds[0])
                print("\n\n\nСтавим зависимости\n\n\n")
                start_prepare(cmds[1])
                print("\n\nПерезагружаем ВМ\n\n\n")
                start_prepare(reboot=1)

                # Disk
                vms_dates_disk = {}
                min_gb = 10
                for name, cfg in self.original_vms_date.items():
                    if "disk" not in cfg:
                        continue
                    disk_gb = int(cfg["disk"])
                    if disk_gb <= min_gb:
                        for i in range(5):
                            print(
                                f"\n\nВНИМАНИЕ для ВМ: {name}, был указан размер диска меньше минимального: {min_gb}. Изменение размера произведено не будет!!!"
                            )
                            continue
                    vms_dates_disk[name] = cfg.copy()

                if vms_dates_disk != {}:
                    vm_str = ""
                    for vm in vms_dates_disk.keys():
                        vm_str = vm_str + vm + " "
                    print(f"Изменяем размер диска для следующих ВМ: {vm_str}")
                    self._resize_disk(vms_date=vms_dates_disk)
                    print("\n==> Повторное получение IP адресов...")

                    with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
                        ip_futures = [
                            executor.submit(self._get_ip, hostname, system_commands)
                            for hostname in self.vms_date.keys()
                        ]
                        for future in as_completed(ip_futures):
                            hostname, ip = future.result()
                            self.vms_date[hostname]["ip_bridge"] = ip

                # Bridge Ip
                if bridge:
                    vms_dates_bridge = {}
                    for name, cfg in self.original_vms_date.items():
                        if "ip_bridge" in cfg:
                            vms_dates_bridge[name] = cfg.copy()

                    if vms_dates_bridge != {}:
                        vm_str = ""
                        for vm in vms_dates_bridge.keys():
                            vm_str = vm_str + vm + " "
                        print(
                            f"Устанавливаем bridge_net и ip адреса для следующих ВМ: {vm_str}"
                        )
                        self._set_ip_bridge(vms_date=vms_dates_bridge)

            else:
                print("\n\n\nСтавим hostname\n\n\n")
                start_prepare(cmds[0])
                print("\n\n\nСтавим репозиторий\n\n\n")
                start_prepare(cmds[1])
                print("\n\n\nAtra Update\n\n\n")
                start_prepare(cmds[2])
                print("\n\n\nСтавим зависимости\n\n\n")
                start_prepare(cmds[3])
                if self.kernel:
                    print("\n\n\nСтавим ядро\n\n\n")
                    start_prepare(cmds[4])
                    print("\n\n\nОбновляем grub\n\n\n")
                    start_prepare(cmds[5])
                    start_prepare(cmds[6])
                    start_prepare(cmds[7])
                    start_prepare(cmds[8])
                else:
                    print(
                        "\n\n\nЯдро не указано пропускаем установку ядра и обновление grub\n\n"
                    )

                print("\n\nПерезагружаем ВМ\n\n\n")
                start_prepare(reboot=1)

                # Disk
                vms_dates_disk = {}
                min_gb = 15
                for name, cfg in self.original_vms_date.items():
                    if "disk" not in cfg:
                        continue
                    disk_gb = int(cfg["disk"])
                    if disk_gb <= min_gb:
                        for i in range(5):
                            print(
                                f"\n\nВНИМАНИЕ для ВМ: {name}, был указан размер диска меньше минимального: {min_gb}. Изменение размера произведено не будет!!!"
                            )
                            continue
                    vms_dates_disk[name] = cfg.copy()

                if vms_dates_disk != {}:
                    vm_str = ""
                    for vm in vms_dates_disk.keys():
                        vm_str = vm_str + vm + " "
                    print(f"Изменяем размер диска для следующих ВМ: {vm_str}")
                    self._resize_disk(vms_date=vms_dates_disk)
                    print("\n==> Повторное получение IP адресов...")

                    with ThreadPoolExecutor(max_workers=len(self.vms_date)) as executor:
                        ip_futures = [
                            executor.submit(self._get_ip, hostname, system_commands)
                            for hostname in self.vms_date.keys()
                        ]
                        for future in as_completed(ip_futures):
                            hostname, ip = future.result()
                            self.vms_date[hostname]["ip_bridge"] = ip

                # Bridge Ip
                if bridge:
                    vms_dates_bridge = {}
                    for name, cfg in self.original_vms_date.items():
                        if "ip_bridge" in cfg:
                            vms_dates_bridge[name] = cfg.copy()

                    if vms_dates_bridge != {}:
                        vm_str = ""
                        for vm in vms_dates_bridge.keys():
                            vm_str = vm_str + vm + " "
                        print(
                            f"Устанавливаем bridge_net и ip адреса для следующих ВМ: {vm_str}"
                        )
                        self._set_ip_bridge(vms_date=vms_dates_bridge)

        return self.vms_date
