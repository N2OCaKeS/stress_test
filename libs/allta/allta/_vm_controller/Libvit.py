from .._system_command.SystemCommands import SystemCommands as system_commands
from ._libs._ssh_command import _SSH_Command as ssh_command
from ._libs._scp_command import _SCP_Command as scp_command
from ._libs._signals import _Signals as signals

from ._base_commands._apt._apt_prorocol import _AptManagerProtocol
from ._base_commands._apt import _apt

from ._base_commands._reboot._reboot import _Reboot as reboot
from ._base_commands._sed._sed import _Sed as sed
from ._base_commands._set_hosts._set_hosts import _SetHosts as set_hosts
from ._base_commands._freeipa._freeipa import _Freeipa as freeipa


from ._vm._virt_install import _VirtInstall
from ._vm._virtual_machine import _VirtualMashines
from ._vm.LibvirtManager import LibvirtManager as libvirt_manager


from typing import cast, Optional, Any, List
import sys
import threading
from time import sleep


class Libvirt(_VirtualMashines):
    """
    Класс VBox предоставляет интерфейс для управления виртуальными машинами (ВМ) с использованием virt-install и Libvirt.

    Основные функции:
    - Подготовка окружения для работы с ВМ.
    - Создание и настройка ВМ на основе Vagrantfile.
    - Проверка доступности ВМ через ping.
    - Выполнение команд на ВМ.
    - Копирование файлов между локальной системой и ВМ.
    - Управление пакетами на ВМ через apt.
    - Настройка файла /etc/hosts на ВМ.

    Этот класс наследуется от абстрактного класса `_VirtualMashines` и реализует его методы.
    """

    # def __init__():
    #     pass

    @classmethod
    def prepare(cls) -> int:
        """
        Выполняет подготовку окружения для работы с виртуальными машинами.

        Returns:
            int: Код завершения выполнения команды.
        """
        print("Устанавливаем и настраиваем Libvirt и зависимости...\n\n\n")
        system_commands.cmd_with_returncode(
            "sudo apt update && sudo DEBIAN_FRONTEND=noninteractive apt-get install astra-kvm wget tar sshpass bridge-utils -y"
        )
        system_commands.cmd_with_returncode(
            "sudo usermod -aG kvm,libvirt,libvirt-qemu,libvirt-admin $USER"
        )

        print("\n\n\nНастраиваем сеть для Libvirt...\n\n\n")
        net = system_commands.check_output_command(
            'ip -4 route get 8.8.8.8 | awk \'{for(i=1;i<=NF;i++){if($i=="dev") d=$(i+1); if($i=="src") s=$(i+1)}} END{print s, d}\''
        )

        ip, phy_if = net.strip().split()
        bridge = "br0"
        if phy_if != bridge:
            system_commands.check_output_command(
                "sudo cp /etc/network/interfaces /etc/network/interfaces.bak || true"
            )
            cfg = f"""sudo tee /etc/network/interfaces > /dev/null <<EOF
auto lo
iface lo inet loopback

auto {bridge}
iface {bridge} inet static
    address {ip}
    netmask 255.255.255.0
    gateway 10.177.103.254
    dns-nameservers 10.177.180.246 10.177.181.142
    bridge_ports {phy_if}
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0

iface {phy_if} inet manual
EOF
"""
            system_commands.cmd_with_returncode(cfg)
            system_commands.cmd_with_returncode(
                f"sudo ifdown {phy_if} || true && sudo ifup {bridge} && sudo systemctl restart networking"
            )
            # Переключаем iptables на nft
            system_commands.cmd_with_returncode(
                "sudo update-alternatives --set iptables /usr/sbin/iptables-nft"
            )
            system_commands.cmd_with_returncode(
                "sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-nft"
            )
            # Настраиваем br_netfilter
            system_commands.cmd_with_returncode("sudo modprobe br_netfilter")
            system_commands.cmd_with_returncode(
                "sudo sysctl -w net.bridge.bridge-nf-call-iptables=1"
            )
            system_commands.cmd_with_returncode(
                "sudo sysctl -w net.bridge.bridge-nf-call-ip6tables=1"
            )
            # Чистим старые правила
            system_commands.cmd_with_returncode(
                "sudo iptables-legacy -F FORWARD || true"
            )
            # Разрешаем форвардинг через мост
            system_commands.cmd_with_returncode(
                "sudo iptables -I FORWARD 1 -i br0 -j ACCEPT"
            )
            system_commands.cmd_with_returncode(
                "sudo iptables -I FORWARD 2 -o br0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
            )
            system_commands.cmd_with_returncode(
                "sudo systemctl disable --now firewalld"
            )
            system_commands.cmd_with_returncode("sudo nft flush ruleset")
            system_commands.cmd_with_returncode(
                f"sudo ifdown {bridge} || true && sudo ifup {bridge} && sudo systemctl restart networking"
            )
            sleep(120)
            print("\n\n\nПроверяем наличие Docker...\n\n\n")
            docker_rc = system_commands.cmd_with_returncode(
                "dpkg -l docker.io >/dev/null 2>&1"
            )
            compose_rc = system_commands.cmd_with_returncode(
                "dpkg -l docker-compose >/dev/null 2>&1"
            )
            if docker_rc == 0 and compose_rc == 0:
                print("Docker уже установлен, шаг установки пропущен.")
            else:
                print("\n\n\nУстанавливаем Docker...\n\n\n")
                system_commands.cmd_with_returncode(
                    "sudo apt-get install -y docker.io docker-compose"
                )

            print("\n\n\nПрименяем настройки для firewall...\n\n\n")

            # Защищаем мост от Docker
            system_commands.cmd_with_returncode(
                "sudo iptables -I DOCKER-USER 1 -i br0 -j ACCEPT"
            )
            system_commands.cmd_with_returncode(
                "sudo iptables -I DOCKER-USER 1 -o br0 -j ACCEPT"
            )
            system_commands.cmd_with_returncode(
                "sudo systemctl disable --now firewalld"
            )
            system_commands.cmd_with_returncode("sudo nft flush ruleset")
            sleep(120)

            print("\n\n\nИсправляю ошибку с cgroup...\n\n\n")
            system_commands.cmd_with_returncode(
                'echo \'cgroup_controllers = [ "cpu", "devices", "memory", "blkio", "cpuacct" ]\' | sudo tee -a /etc/libvirt/qemu.conf'
            )

            print("\n\n\nПерезапускаем сервисы...\n\n\n")
            system_commands.cmd_with_returncode("sudo systemctl restart libvirtd")
            system_commands.cmd_with_returncode("sudo systemctl restart docker")
        return 0

    @classmethod
    def build(
        cls,
        box: str,
        rc: str,
        vms,
        vms_dates: dict,
        kernel: Optional[str] = None,
        bridge: bool = False,
    ) -> dict:
        """
        Создаёт и настраивает виртуальные машины на основе Vagrantfile.

        Args:

            box (str): Имя образа (бокса).
            vms (list): Список имён виртуальных машин.

                vms = ['hostname1', 'hostname2']
            rc (str, optional): Версия операционной системы. Если не указан, не используется.
            prepare_path (str, optional): Путь к файлу подготовки окружения. Если не указан, не используется.
            vms_date (dict): Полная информация о виртуальных машинах.

                vm_dates = {
                    'hostname':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        'cpu': ""
                        }
                    }
            kernel (str, optional): То какое ядро необходимо установить (полный вывод uname -r), если не задано то оставит ядро по умолчанию
            bridge (bool, optional): Настроить ли мост по тем ip адресам что указаны в vms_dates, по умолчанию выключено.
        Returns:
            dict: Обновленный vms_dates (ИСПОЛЬЗОВАТЬ ТОЛЬКО ДЛЯ ВНУТРЕННЕЙ СЕТИ Libvirt).
        """

        virt = _VirtInstall(box=box, vms_date=vms_dates, rc=rc, kernel=kernel or "")
        vms_dates = virt.build(bridge=bridge)

        if box != "vm_station":
            libvirt_manager.Snapshot.create(vms=vms, snapshot_name="build")
        system_commands.cmd("virsh -c qemu:///system list --all")
        return vms_dates

    @classmethod
    def check(
        cls, vms: list, vms_dates: dict, ping_retries: int = 5, ping_timeout_s: int = 1
    ):
        """
        Проверяет доступность виртуальных машин через ping.

        Args:
            vms (list): Список имён виртуальных машин.

                vms = ['hostname1', 'hostname2']

            vms_date (list): Полная информация о виртуальных машинах.

                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }
                    }
            ping_retries (int): Кол-во попыток для проверки. По умолчанию 3.
            ping_timeout_s (int): Таймауты для проверок в секундах. По умолчанию 1.

        Returns:
            int: 0, если все машины доступны, иначе 1.
        """
        if not vms:
            print("Список ВМ пуст — нечего проверять.")
            return 0

        failed = []

        for vm in vms:
            if vm not in vms_dates:
                failed.append(f"{vm} (нет записи в vms_dates)")
                continue

            ip = vms_dates[vm].get("ip_bridge")
            if not ip:
                failed.append(f"{vm} (не задан ip_bridge)")
                continue

            ok = False
            for _ in range(ping_retries):
                rc = system_commands.cmd_with_returncode(
                    f"ping -c 1 -W {ping_timeout_s} {ip}"
                )
                if rc == 0:
                    ok = True
                    break
            if not ok:
                failed.append(f"{vm} @ {ip}")

        if failed:
            print("Недоступны ВМ по ping:")
            for item in failed:
                print(f"  - {item}")
            print("Прерываю выполнение")
            sys.exit(1)

        print("All VMs are available (ping)")
        return 0

    @classmethod
    def execute(
        cls,
        commands: dict,
        vms_dates: dict,
        vms_groups: Optional[dict] = None,
        username: str = "u",
        password: str = "1",
        timeout: int = 15,
    ) -> int:
        """
        Выполняет команды на виртуальных машинах. Если имя задачи равно "reboot", то производится
        перезагрузка с ожиданием готовности ВМ. При выполнении команды для группы ВМ перезагрузка
        производится для всей группы, и сигнал устанавливается только когда все ВМ из группы готовы.

        Args:
            commands (dict): Словарь с командами для выполнения.

                commands = {
                    'hostname1':{
                            'task1':{
                                'command':"",
                                'signal set': 'test' # Если не надо ставить оставить пустым
                                'signal get': '' # Если не надо получать оставить пустым
                            },
                            interective_task3: {
                                "command": "sudo perf record -g -a", # Interective task, автоматически закончиться спустя указанное время
                                "signal get": "1",
                                "nowait": True,  # default = False
                                "nowait_timeout": 15,  # default = 30 sec
                            }
                        },
                    'g_group1':{ # Если выполнять на группе хостов необходимо указать в виде g_<groupname>
                            'task2':{
                                'command':"",
                                'signal set': '' # Если не надо ставить оставить пустым
                                'signal get': ['test'] # Ищет для каждого хоста из группы хостов
                            },
                            'task2':{
                                'command':"",
                                'signal set': '' # Если не надо ставить оставить пустым
                                'signal get': ['hostname1' ,'test'] # Ищет для конкретного хоста
                            },
                            'reboot':{ # Перезагрузит ВМ
                                'signal set': ''
                                'signal get': ['hostname1' ,'test'] # Ищет для конкретного хоста
                            },
                    }
            vms_date (list): Полная информация о виртуальных машинах.

                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }
                    }
            vms_groups (dict, optional): Группы виртуальных машин.

                vms_groups = {
                    'group1':['hostname1', 'hostname2'],
                    }
            username (str, optional): Имя пользователя для SSH.
            password (str, optional): Пароль для SSH.
            timeout (int. optional): timeout ожидания сигнала в минутах

        Returns:
            int: Код завершения выполнения.
        """

        def _normalize_signal_get(raw: Any) -> Optional[List[str]]:
            if raw is None or raw == "":
                return None
            if isinstance(raw, str):
                return [raw]
            if isinstance(raw, list):
                return raw[:]
            return [str(raw)]

        threads = []

        def _threaded_execution(
            host: str, task_name: str, task: dict, username: str, password: str
        ):
            sig_get = _normalize_signal_get(task.get("signal get"))
            sig_set = cast(Optional[str], task.get("signal set"))

            if task_name.lower() == "reboot":
                reboot_status = reboot.reboot_vm(
                    host,
                    vms_dates,
                    username,
                    password,
                    signal_get=sig_get,  # type: ignore[arg-type]
                    ready_signal=sig_set,  # type: ignore[arg-type]
                )
                if not reboot_status:
                    print(f"Перезагрузка {host} не удалась.")
                return

            # per-task nowait
            if bool(task.get("nowait", False)):
                nwt = task.get("nowait_timeout", 30)
                try:
                    nwt = int(nwt)
                except Exception:
                    nwt = 30

                ssh_command.cmd_detach(
                    host=host,
                    command=task["command"],
                    username=username,
                    password=password,
                    vm_dates=vms_dates,
                    signal_set=sig_set,  # type: ignore[arg-type]
                    signal_get=sig_get,  # None -> не ждём сигнал в _SSH_Command # type: ignore[arg-type]
                    task_name=task_name,
                    time_out=timeout,
                    nowait_timeout=nwt,
                )
                return

            # обычный синхронный путь
            ssh_command.cmd(
                host=host,
                command=task["command"],
                username=username,
                password=password,
                vm_dates=vms_dates,
                signal_set=sig_set,  # type: ignore[arg-type]
                signal_get=sig_get,  # type: ignore[arg-type]
                task_name=task_name,
                time_out=timeout,
            )

        # Разворачиваем команды по целям
        for target, tasks in commands.items():
            if target.startswith("g_"):
                group_name = target[2:]
                if vms_groups and group_name in vms_groups:
                    for task_name, task in tasks.items():
                        if task_name.lower() == "reboot":

                            def group_worker():
                                reboot.reboot_group(
                                    vms_groups[group_name],
                                    vms_dates,
                                    username,
                                    password,
                                    signal_get=_normalize_signal_get(
                                        task.get("signal get")
                                    ),  # type: ignore[arg-type]
                                    ready_signal=cast(Optional[str], task.get("signal set")),  # type: ignore[arg-type]
                                )

                            t = threading.Thread(target=group_worker)
                            threads.append(t)
                            t.start()
                        else:
                            for host in vms_groups[group_name]:
                                t = threading.Thread(
                                    target=_threaded_execution,
                                    args=(host, task_name, task, username, password),
                                )
                                threads.append(t)
                                t.start()
                else:
                    print(f"Группа '{group_name}' не найдена в vms_groups.")
            else:
                host = target
                for task_name, task in tasks.items():
                    if task_name.lower() == "reboot":
                        t = threading.Thread(
                            target=lambda: reboot.reboot_vm(
                                host,
                                vms_dates,
                                username,
                                password,
                                signal_get=_normalize_signal_get(
                                    task.get("signal get")
                                ),  # type: ignore[arg-type]
                                ready_signal=cast(Optional[str], task.get("signal set")),  # type: ignore[arg-type]
                            )
                        )
                        threads.append(t)
                        t.start()
                    else:
                        t = threading.Thread(
                            target=_threaded_execution,
                            args=(host, task_name, task, username, password),
                        )
                        threads.append(t)
                        t.start()

        for t in threads:
            t.join()

        signals.remove_all()
        return 0

    @classmethod
    def scp(
        cls,
        scp_settings: dict,
        vms_dates: dict,
        vms_groups: Optional[dict] = None,
        username: str = "u",
        password: str = "1",
    ) -> int:
        """
        Выполняет копирование файлов между локальной системой и виртуальными машинами.

        Args:
            scp_settings (dict): Настройки для копирования файлов.

                scp_settings = {
                    'hostname1': [
                        {
                            'mode': 'push', # Режимы: push - отправить на ВМ; pull - получить из ВМ
                            'path_host': '',
                            'path_vm': ''
                        }
                    ]
                    'g_group1':[ # Если выполнять на группе хостов необходимо указать в виде g_<groupname>
                        {
                            'mode': 'push', # Режимы: push - отправить на ВМ; pull - получить из ВМ
                            'path_host': '',
                            'path_vm': ''
                        },
                        {
                            'mode': 'push', # Режимы: push - отправить на ВМ; pull - получить из ВМ
                            'path_host': '',
                            'path_vm': ''
                        },
                    ]
            vms_date (list): Полная информация о виртуальных машинах.

                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }
                    }
            vms_groups (dict, optional): Группы виртуальных машин.

                vms_groups = {
                    'group1':['hostname1', 'hostname2'],
                    }
            username (str, optional): Имя пользователя для SSH. По умолчанию "u".
            password (str, optional): Пароль для SSH. По умолчанию "1".

        Returns:
            int: Код завершения выполнения.
        """

        scp_command.execute(
            scp=scp_settings,
            vms_date=vms_dates,
            groups=vms_groups or {},
            username=username,
            password=password,
        )
        return 0

    @classmethod
    def set_hosts(
        cls, domain: str, vms_dates: dict, username: str = "u", password: str = "1"
    ):
        """
        Настраивает файл /etc/hosts на всех указанных виртуальных машинах.

        Args:
            domain (str): Домен для формирования FQDN.
            vms_date (list): Полная информация о виртуальных машинах.

                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }
                    }
            username (str, optional): Имя пользователя для подключения по SSH. По умолчанию "u".
            password (str, optional): Пароль для подключения по SSH. По умолчанию "1".
        """

        set_hosts.set_hosts(
            domain=domain, vms_dates=vms_dates, username=username, password=password
        )
        return 0

    @classmethod
    def sed(
        cls,
        sed_conf: dict,
        vms_dates: dict,
        vms_groups: Optional[dict] = None,
        username: str = "u",
        password: str = "1",
    ):
        """
        Выполняет замену строки в файле

        Args:
            sed_conf (dict): Настройки для копирования файлов.

                sed_conf = {
                    'hostname1':[
                        {
                            'path': '',
                            'old': '',
                            'new': ''
                        },
                        {
                            'path': '',
                            'old': '',
                            'new': ''
                        },
                    ],
                    'g_group1':[ # Если выполнять на группе хостов необходимо указать в виде g_<groupname>
                        {
                            'path': '',
                            'old': '',
                            'new': ''
                        },
                        {
                            'path': '',
                            'old': '',
                            'new': ''
                        },

                    ],
                    }

            vms_date (list): Полная информация о виртуальных машинах.

                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }
                    }
            vms_groups (dict, optional): Группы виртуальных машин.

                vms_groups = {
                    'group1':['hostname1', 'hostname2'],
                    }
            username (str, optional): Имя пользователя для SSH. По умолчанию "u".
            password (str, optional): Пароль для SSH. По умолчанию "1".

        Returns:
            int: Код завершения выполнения.
        """

        sed.sed(
            sed_conf=sed_conf,
            vms_dates=vms_dates,
            groups=vms_groups or {},
            username=username,
            password=password,
        )
        return 0

    @classmethod
    def freeipa(
        cls,
        domain: dict,
        vms_dates: dict,
        vms_groups: Optional[dict] = None,
        username: str = "u",
        password: str = "1",
    ):
        """Развертывает домен freeipa и вводит в него клиенты

        Args:
            domain (dict): Настройка для freeipa
                domain = {
                    'settings': {
                        'domain': 'example.com',
                        'admin_password': 'secret'
                    },
                    'domain': {
                        'host': 'domain'
                    },
                    'client': {
                        'host': 'database'  # Если выполнять на группе хостов необходимо указать в виде g_<groupname>
                    }
                }
            vms_date (list): Полная информация о виртуальных машинах.

                vm_dates = {
                    'hostname1':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        },
                    'hostname2':{
                        'host-port':'*', # порт ssh
                        'ip_bridge':'*.*.*.*', # ip моста
                        }
                    }
            vms_groups (dict, optional): Группы виртуальных машин.

                vms_groups = {
                    'group1':['hostname1', 'hostname2'],
                    }
            username (str, optional): Имя пользователя для SSH. По умолчанию "u".
            password (str, optional): Пароль для SSH. По умолчанию "1".

        Returns:
            _type_: _description_
        """
        freeipa.freeipa(
            domain=domain,
            vm_dates=vms_dates,
            vms_groups=vms_groups or {},
            ssh_user=username,
            ssh_password=password,
        )
        return 0

    apt: _AptManagerProtocol = cast(_AptManagerProtocol, _apt._AptManager())
