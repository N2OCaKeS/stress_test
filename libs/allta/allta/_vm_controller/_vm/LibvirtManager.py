from ..._decorators.Decorators import BaseDecorators
from ..._system_command.SystemCommands import SystemCommands as system_commands
from .._libs._scp_command import _SCP_Command
from .._libs import _scripts as scripts_pkg

from time import sleep

from importlib.resources import files, as_file
import re
import os
import json
from pathlib import Path
from collections import defaultdict


class LibvirtManager:
    """
    Класс для управления виртуальными машинами через libvirt.

    Основные функции:
    - Включение и выключение ВМ.
    - Создание снимков всех настроенных ВМ.
    - Настройка сетевого интерфейса ВМ на мостовой режим с использованием nmcli.
    - Получение списка доступных ВМ.
    """

    class Vm:
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
            return system_commands.check_output_command(
                "virsh --connect qemu:///system list --all"
            )

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
                system_commands.cmd(f"virsh --connect qemu:///system start {vm}")
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
                system_commands.cmd(f"virsh --connect qemu:///system destroy {vm}")
            return 0

        @BaseDecorators.trycorator
        @staticmethod
        def bridge(
            vms_date: dict, new_vms_date: dict, username: str = "u", password: str = "1"
        ):
            """Устанавливает тип соединения bridge на ВМ

            Args:
                vms_dates (dict): Полная информация о виртуальных машинах.
                new_vms_dates (dict): Информация о виртуальных машинах, только с учетом новых ip для bridge
                username (str, optional): Имя пользователя для SSH. По умолчанию "u".
                password (str, optional): Пароль для SSH. По умолчанию "1".
            """
            from ..Libvit import Libvirt

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
    dns-nameservers 10.177.180.246 10.177.128.198
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
            else:
                print(f"У вас уже настроен сетевой интерфес типа мост: {bridge}")

            def get_file_path(filename: str) -> str:
                res = files(scripts_pkg).joinpath(filename)
                with as_file(res) as p:
                    return str(p)

            vms_list = list(vms_date.keys())
            network_path = get_file_path("network.sh")
            provision_path = get_file_path("static_ip.sh")
            group = {"all": vms_list}
            scp_prepare = {
                "g_all": [
                    {
                        "mode": "push",
                        "path_host": f"{provision_path}",
                        "path_vm": "/home/u/static_ip.sh",
                    }
                ],
            }
            _SCP_Command.execute(
                scp=scp_prepare,
                vms_date=vms_date,
                groups=group,
                username=username,
                password=password,
            )
            prepare = {}
            for vm_name in vms_date:
                prepare[vm_name] = {
                    "prepare": {
                        "command": (
                            f"sudo chmod 777 /home/u/static_ip.sh && "
                            f"sudo su -c '/home/u/static_ip.sh {new_vms_date[vm_name]['ip_bridge']}' && "
                            f"sudo rm /home/u/static_ip.sh"
                        ),
                        "signal set": "prepare",
                        "signal get": "",
                    },
                    "conf": {
                        "command": "(sleep 2 && sudo shutdown -r now) &",
                        "signal set": "",
                        "signal get": ["prepare"],
                    },
                }

            Libvirt.execute(
                commands=prepare,
                vms_dates=vms_date,
                vms_groups=group,
                username=username,
                password=password,
            )
            for vm in vms_list:
                commands = f"{network_path} {vm}"
                system_commands.cmd_with_returncode(commands)
            print("Ожидаем включения ВМ")
            sleep(60)
            return 0

        @BaseDecorators.trycorator
        @staticmethod
        def additional_disk(
            vms_dates: dict,
            disk_path: str = "/vms/additional_disk",
            disk_pool_name: str = "additional",
            username: str = "u",
            password: str = "1",
        ):
            """Подключение дополнительных дисков к ВМ с монтированием

            Args:
                vms_dates (dict): Информация о ВМ
                disk_path (str, optional): Путь по которому будут создаваться новые диски. По умолчанию "/vms/additional_disk".
                disk_pool_name (str, optional): Название libvirt disk pool. По умолчанию "additional".
                username (str, optional): Имя пользователя. Defaults to "u".
                password (str, optional): Пароль. Defaults to "1".


            Returns:
                int: Всегда возвращает 0 смотреть логи
            """
            from ..Libvit import Libvirt
            def get_file_path(filename: str) -> str:
                res = files(scripts_pkg).joinpath(filename)
                with as_file(res) as p:
                    return str(p)

            def pick_target(vm_name: str, used_overrides: set | None = None) -> str:
                """
                Читает текущее состояние домена и возвращает первый свободный vd[b-z].
                Объединяет занятость из virsh и локальные 'used_overrides'.
                """
                domblk = system_commands.check_output_command(
                    f"sudo virsh --connect qemu:///system domblklist {vm_name} --details || true"
                )
                used = set()
                for line in domblk.splitlines():
                    parts = line.split()

                    if len(parts) >= 3 and parts[0] in ("file", "block", "network"):
                        used.add(parts[2])
                if used_overrides:
                    used |= set(used_overrides)

                for code in range(ord("b"), ord("z") + 1):
                    t = f"vd{chr(code)}"
                    if t not in used:
                        return t
                raise RuntimeError("Не удалось подобрать свободный target (vd[b-z])")

            def host_block_exists(path: str) -> bool:
                return system_commands.check_output_command(
                    f"test -b {path} && echo yes || echo no"
                ).strip() == "yes"

            # === подготовка пути/пула (для qcow2) ===
            script_path = get_file_path("additional_disk.sh")
            default_disk_size_gb = 10

            tasks: dict = {}
            scp: dict = {}

            system_commands.check_output_command(
                f"sudo mkdir -p {disk_path} && sudo chmod 777 {disk_path}"
            )
            pool_list = system_commands.check_output_command(
                "sudo virsh --connect qemu:///system pool-list --all || true"
            )
            if disk_pool_name not in pool_list:
                system_commands.check_output_command(
                    f"sudo virsh --connect qemu:///system pool-define-as {disk_pool_name} dir --target {disk_path}"
                )
            system_commands.check_output_command(
                f"sudo virsh --connect qemu:///system pool-build {disk_pool_name} || true"
            )
            system_commands.check_output_command(
                f"sudo virsh --connect qemu:///system pool-start {disk_pool_name} || true"
            )
            system_commands.check_output_command(
                f"sudo virsh --connect qemu:///system pool-autostart {disk_pool_name} || true"
            )
            system_commands.check_output_command(
                f"sudo virsh --connect qemu:///system pool-refresh {disk_pool_name} || true"
            )

            for vm, cfg in (vms_dates or {}).items():
                additional_disks = (cfg or {}).get("additional_disks") or {}
                if not isinstance(additional_disks, dict) or not additional_disks:
                    continue

                tasks.setdefault(vm, {})
                signal_counter = 1
                prev_signal = ""
                have_guest_tasks_for_vm = False

                used_targets = set()
                _cur = system_commands.check_output_command(
                    f"sudo virsh --connect qemu:///system domblklist {vm} --details || true"
                )
                for line in _cur.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] in ("file", "block", "network"):
                        used_targets.add(parts[2])

                for disk_name, dcfg in additional_disks.items():
                    dcfg = dcfg or {}

                    host_path = (dcfg.get("device") or "").strip()
                    fs_type = (dcfg.get("fs_type") or "").strip()
                    mount_point = (dcfg.get("mount_point") or "").strip()
                    serial = f"{vm}_{disk_name}"

                    if host_path:
                        # ======== ФИЗИЧЕСКОЕ УСТРОЙСТВО ========
                        if not host_block_exists(host_path):
                            raise RuntimeError(f"{host_path} не является блочным устройством на хосте")

                        attached_line = system_commands.check_output_command(
                            f"sudo virsh --connect qemu:///system domblklist {vm} --details | grep -F '{host_path}' || true"
                        )
                        if host_path not in attached_line:
                            target = pick_target(vm, used_targets)
                            system_commands.check_output_command(
                                f"sudo virsh --connect qemu:///system attach-disk {vm} {host_path} {target} "
                                f"--persistent --driver qemu --targetbus virtio --serial {serial}"
                            )
                            used_targets.add(target)

                        if fs_type or mount_point:
                            if not have_guest_tasks_for_vm:
                                scp.setdefault(vm, []).append({
                                    "mode": "push",
                                    "path_host": script_path,
                                    "path_vm": "/tmp/additional_disk.sh",
                                })
                                have_guest_tasks_for_vm = True

                            fs_arg = fs_type if fs_type else "KEEPFS"
                            cmd = f"sudo /tmp/additional_disk.sh {serial} {fs_arg}"
                            if mount_point:
                                cmd += f" {mount_point}"

                            task_name = f"additional disk {disk_name}"
                            tasks[vm][task_name] = {
                                "command": cmd,
                                "signal set": str(signal_counter),
                                "signal get": prev_signal,
                            }
                            prev_signal = str(signal_counter)
                            signal_counter += 1

                    else:
                        size_raw = dcfg.get("size", default_disk_size_gb)
                        try:
                            size_gb = int(str(size_raw).strip())
                        except Exception:
                            size_gb = default_disk_size_gb

                        qcow_path = os.path.join(disk_path, f"{vm}_{disk_name}.qcow2")
                        exists = system_commands.check_output_command(
                            f"test -f {qcow_path} && echo yes || echo no"
                        )
                        if exists.strip() != "yes":
                            system_commands.check_output_command(
                                f"sudo qemu-img create -f qcow2 {qcow_path} {size_gb}G"
                            )
                            system_commands.check_output_command(
                                f"sudo chmod 666 {qcow_path} || true"
                            )

                        attached_line = system_commands.check_output_command(
                            f"sudo virsh --connect qemu:///system domblklist {vm} --details | grep -F '{qcow_path}' || true"
                        )
                        if qcow_path not in attached_line:
                            target = pick_target(vm, used_targets)
                            system_commands.check_output_command(
                                f"sudo virsh --connect qemu:///system attach-disk {vm} {qcow_path} {target} "
                                f"--persistent --driver qemu --subdriver qcow2 --targetbus virtio --serial {serial}"
                            )
                            used_targets.add(target)

                        if not have_guest_tasks_for_vm:
                            scp.setdefault(vm, []).append({
                                "mode": "push",
                                "path_host": script_path,
                                "path_vm": "/tmp/additional_disk.sh",
                            })
                            have_guest_tasks_for_vm = True

                        fs_for_qcow = fs_type if fs_type else "ext4"
                        cmd = f"sudo /tmp/additional_disk.sh {serial} {fs_for_qcow}"
                        if mount_point:
                            cmd += f" {mount_point}"

                        task_name = f"additional disk {disk_name}"
                        tasks[vm][task_name] = {
                            "command": cmd,
                            "signal set": str(signal_counter),
                            "signal get": prev_signal,
                        }
                        prev_signal = str(signal_counter)
                        signal_counter += 1

                if prev_signal:
                    tasks[vm]["reboot"] = {"signal get": prev_signal}

            if scp:
                Libvirt.scp(
                    scp_settings=scp,
                    vms_dates=vms_dates,
                    username=username,
                    password=password,
                )
            if tasks:
                Libvirt.execute(
                    commands=tasks,
                    vms_dates=vms_dates,
                    username=username,
                    password=password,
                )

            return 0

        @BaseDecorators.trycorator
        @staticmethod
        def save_vms_data(vms_dates: dict, save_path: str = "./vms_dates.json"):
            """Сохраняет информацию о ВМ

            Args:
                vms_data (dict): Список ВМ с информацией об ip_bridge
                save_path (str, optional): В какой файл сохранить информацию. По умолчанию "./vms_dates.json".

            Returns:
                _type_: int
            """
            try:
                if not isinstance(vms_dates, dict):
                    print("save_vms_data: vms_data должен быть dict")
                    return 1

                Path(save_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

                with open(save_path, "w", encoding="UTF-8") as f:
                    json.dump(vms_dates, f, ensure_ascii=False, indent=2)
                return 0

            except TypeError as e:
                print(f"save_vms_data: данные не сериализуемы в JSON: {e}")
                return 1

            except OSError as e:
                print(f"save_vms_data: не удалось записать файл '{save_path}': {e}")
                return 1

        @BaseDecorators.trycorator
        @staticmethod
        def load_vms_data(save_path: str = "./vms_dates.json"):
            """Загружает инфрмация о ВМ

            Args:
                save_path (str, optional): Из какого файла загрузить информацию. По умолчанию "./vms_dates.json".

            Returns:
                _type_: dict
            """
            try:
                path = Path(save_path)
                if not path.exists():
                    print(f"load_vms_data: файл не найден: {path}")
                    return {}

                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    print("load_vms_data: в файле должен быть JSON-объект (dict)")
                    return {}

                return data

            except json.JSONDecodeError as e:
                print(f"load_vms_data: некорректный JSON в '{save_path}': {e}")
                return {}

            except OSError as e:
                print(f"load_vms_data: ошибка чтения '{save_path}': {e}")
                return {}

            except Exception as e:
                print(f"load_vms_data: непредвиденная ошибка: {e}")
                return {}

    class Snapshot:
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
                    system_commands.cmd(
                        f'virsh --connect qemu:///system snapshot-create-as --domain {vm} --name "{snapshot_name}"'
                    )
            else:
                for vm in vms:
                    system_commands.cmd(
                        f'virsh --connect qemu:///system snapshot-create-as --domain {vm} --name "snapshot"'
                    )
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
                system_commands.cmd(
                    f'virsh --connect qemu:///system snapshot-delete --domain {vm} --snapshotname "{snapshot_name}"'
                )

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
                system_commands.cmd(
                    f'virsh --connect qemu:///system snapshot-revert --domain {vm} --snapshotname "{snapshot_name}"'
                )

    class Command:
        """Класс для генерации команд"""

        @staticmethod
        def _normalize_commands(command: str | list[str]) -> list[str]:
            if isinstance(command, list):
                return [str(c) for c in command if str(c).strip()]
            return [str(command)]

        @staticmethod
        def _block_from_command(cmd: str) -> str:
            return re.sub(r"\s+", "_", cmd.strip())

        @BaseDecorators.trycorator
        @staticmethod
        def generator(
            command: str | list,
            target: str,
            sync: bool = True,
            block_name: str | None = None,
        ):
            """Генерирует команды

            Args:
                command (str | list): Команда или список из команд
                target (str): ВМ(передавать имя) или группа ВМ (передавать как g_<имя группы>) на которой должны быть выполнены команды
                sync (bool, optional): Выполнять последовательно или асинхронно. По умолчанию последовательно True.
                name_block (str, optional): Имя блока команд. По умолчанию будут ставиться команда с замененными " " на "_".

            Returns:
                dict: готовый список команд, для исполнения через Libvirt.execute()
            """
            cmds = LibvirtManager.Command._normalize_commands(command)
            n = len(cmds)
            blocks = {}
            seen_names = defaultdict(int)

            provided_name = block_name

            for i, cmd in enumerate(cmds, start=1):
                if provided_name is None:
                    base = LibvirtManager.Command._block_from_command(cmd)
                    seen_names[base] += 1
                    this_block_name = base if seen_names[base] == 1 else f"{base}_{i}"
                else:
                    base = str(provided_name)
                    this_block_name = base if n == 1 else f"{base} [{i}]"

                if i == 1:
                    sig_get = ""
                else:
                    sig_get = [f"{i - 1}"] if sync else ""

                sig_set = f"{i}" if (sync and i < n) else ""

                blocks[this_block_name] = {
                    "command": cmd,
                    "signal set": sig_set,
                    "signal get": sig_get,
                }

            return {str(target): blocks}
