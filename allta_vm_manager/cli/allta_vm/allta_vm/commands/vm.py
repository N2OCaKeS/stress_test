import os
import shutil
from time import sleep
from allta import Libvirt, LibvirtManager, SystemCommands
from allta_vm.commands.snapshot import Snapshot
from allta_vm.config import get_repo, load_vms_dates, edit_vm
from allta_vm.libs.exit_code import ExitCodes


class Vm:
    @staticmethod
    def _precheck_libvirt_env() -> int:
        """
        Единый пречек окружения libvirt/KVM:
          1) нужные бинарники в PATH
          2) libvirtd активен
          3) /dev/kvm существует и доступен на R/W
          4) storage pool 'default' активен (если используешь другой — замени)
        Всё захардкожено, без параметров.
        """
        required_bins = [
            "virsh",
            "virt-install",
            "qemu-img",
            "sshpass",
            "scp",
        ]
        missing = []
        for b in required_bins:
            rc = SystemCommands.cmd_with_returncode(f"command -v {b} >/dev/null 2>&1")
            if rc != 0:
                missing.append(b)
        if missing:
            print(f"[PRECHECK] Не найдены утилиты: {', '.join(missing)}")
            return ExitCodes.PRECHECK_MISSING_DEPS

        # 2) libvirtd активен
        rc = SystemCommands.cmd_with_returncode("systemctl is-active --quiet libvirtd")
        if rc != 0:
            print("[PRECHECK] Сервис libvirtd не активен. Запустите: sudo systemctl enable --now libvirtd")
            return ExitCodes.PRECHECK_SERVICE

        # 3) /dev/kvm
        if not os.path.exists("/dev/kvm"):
            print("[PRECHECK] Не найден /dev/kvm. Включите аппаратную виртуализацию и установите KVM.")
            return ExitCodes.PRECHECK_KVM
        if not os.access("/dev/kvm", os.R_OK | os.W_OK):
            print("[PRECHECK] Нет доступа к /dev/kvm. Добавьте пользователя в группы kvm/libvirt и перелогиньтесь.")
            return ExitCodes.PRECHECK_KVM

        return ExitCodes.OK

    @staticmethod
    def _require_names_absent(vms: list[str]) -> int:
        """Перед созданием: убеждаемся, что таких доменов ещё нет."""
        busy = []
        for name in vms:
            rc = SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system dominfo '{name}' >/dev/null 2>&1")
            if rc == 0:
                busy.append(name)
        if busy:
            print(f"[PRECHECK] Эти ВМ уже существуют и мешают созданию: {', '.join(busy)}")
            return ExitCodes.PRECHECK_NAME_CONFLICT
        return ExitCodes.OK

    @staticmethod
    def _require_names_exist(vms: list[str]) -> int:
        """Перед удалением/обновлением/стартом/стопом: ВМ должны существовать."""
        missing = []
        for name in vms:
            rc = SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system dominfo '{name}' >/dev/null 2>&1")
            if rc != 0:
                missing.append(name)
        if missing:
            print(f"[PRECHECK] Эти ВМ не существуют: {', '.join(missing)}")
            return ExitCodes.PRECHECK_NAME_CONFLICT
        return ExitCodes.OK

    @staticmethod
    def _check_space_images_dir(required_gb: int) -> int:
        """Простой чек свободного места."""
        path = "/vms"
        usage = shutil.disk_usage(path)
        free_gb = usage.free // (1024**3)
        if free_gb < required_gb:
            print(f"[PRECHECK] Недостаточно места в {path}: нужно ~{required_gb} ГБ, доступно ~{free_gb} ГБ.")
            return ExitCodes.PRECHECK_NO_SPACE
        return ExitCodes.OK

    @staticmethod
    def _post_expect_exists(vms: list[str], must_exist: bool) -> int:
        for name in vms:
            rc = SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system dominfo '{name}' >/dev/null 2>&1")
            exists = (rc == 0)
            if must_exist and not exists:
                print(f"[POSTCHECK] Ожидали, что ВМ '{name}' существует, но её нет.")
                return ExitCodes.VERIFY_FAILED
            if not must_exist and exists:
                print(f"[POSTCHECK] Ожидали, что ВМ '{name}' удалена, но она всё ещё определена.")
                return ExitCodes.VERIFY_FAILED
        return ExitCodes.OK

    @staticmethod
    def _post_expect_running(vms: list[str], must_run: bool) -> int:
        for name in vms:
            rc = SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system domstate '{name}' | grep -q работает")
            running = (rc == 0)
            if must_run and not running:
                print(f"[POSTCHECK] Ожидали, что ВМ '{name}' запущена, но она выключена.")
                return ExitCodes.VERIFY_FAILED
            if not must_run and running:
                print(f"[POSTCHECK] Ожидали, что ВМ '{name}' остановлена, но она работает.")
                return ExitCodes.VERIFY_FAILED
        return ExitCodes.OK

    # ===== твои процедуры ниже — с интеграцией проверок =====

    def _change_pass(vms_dates, new_password):
        passwd = {}
        for vm_name in vms_dates:
            passwd[vm_name] = {
                'set password':{
                    'command': f"yes \"{new_password}\" | sudo passwd u",
                    'signal set': '', 
                    'signal get': ''                    
                }  
            }
        Libvirt.execute(commands=passwd, vms_dates=vms_dates, username='u', password='1')

    def create(info_path, box: str = "vm_station", rc: str = None, kernel: str = None, new_password: str = "1"):
        try:
            SystemCommands.cmd_with_returncode("sudo systemctl enable --now libvirtd")
            rc_env = Vm._precheck_libvirt_env()
            if rc_env != ExitCodes.OK:
                return rc_env

            vms_dates = load_vms_dates(info_path=info_path)
            vms_list = list(vms_dates.keys())

            rc_names = Vm._require_names_absent(vms_list)
            if rc_names != ExitCodes.OK:
                return rc_names

            rc_space = Vm._check_space_images_dir(100 * len(vms_list) + 115 )
            if rc_space != ExitCodes.OK:
                return rc_space

            lv = Libvirt()
            new_vms_dates = lv.build(box=box, rc=rc, vms=vms_list, vms_dates=vms_dates, kernel=kernel)

            group = {'all': vms_list}
            rc_list = ["1.7.5.9", "1.8.1.6"]

            for rc_ver in rc_list:
                LibvirtManager.Snapshot.revert(vms=vms_list, snapshot_name=f"{rc_ver}_build")
                SystemCommands.cmd("sudo rm -rf /root/.ssh/known_hosts ~/.ssh/known_hosts")

                scp_prepare = {
                    "g_all": [
                        {'mode': 'push', 'path_host': '/opt/allta_vm/vm/provision.sh', 'path_vm': '/home/u/env_provision.sh'}
                    ],
                }
                Libvirt.scp(scp_settings=scp_prepare, vms_dates=new_vms_dates, vms_groups=group, username='u', password='1')

                prepare = {}
                for vm_name in vms_dates:
                    prepare[vm_name] = {
                        'set hostname': {
                            'command': (
                                f"sudo hostnamectl set-hostname {vm_name} && "
                                f"if grep -q '^127\\.0\\.1\\.1' /etc/hosts; then "
                                f"sudo sed -i 's/^127\\.0\\.1\\.1.*/127.0.1.1\\t{vm_name}/' /etc/hosts; "
                                f"else echo -e '127.0.1.1\\t{vm_name}' | sudo tee -a /etc/hosts; fi"
                            ),
                            'signal set': 'hostname',
                            'signal get': ''
                        },
                        'prepare': {
                            'command': (
                                f"sudo chmod 777 /home/u/env_provision.sh && "
                                f"sudo su -c '/home/u/env_provision.sh {vms_dates[vm_name]['ip_bridge']}'"
                            ),
                            'signal set': 'prepare',
                            'signal get': ['hostname']
                        },
                        'confirm': {
                            'command': "(sleep 2 && sudo reboot) &",
                            'signal set': '',
                            'signal get': ['prepare']
                        }
                    }
                Libvirt.execute(commands=prepare, vms_dates=new_vms_dates, vms_groups=group, username='u', password='1')

                sleep(90)
                for vm in vms_list:
                    rc_net = SystemCommands.cmd_with_returncode(f"/opt/allta_vm/vm/network.sh {vm}")
                    if rc_net != 0:
                        print(f"[ERROR] Настройка сети для {vm} rc={rc_net}")
                        return ExitCodes.ACTION_FAILED

                LibvirtManager.Snapshot.delete(vms=vms_list, snapshot_name=f"{rc_ver}_build")
                LibvirtManager.Snapshot.create(vms=vms_list, snapshot_name=f"{rc_ver}_build")
                sleep(90)
                Vm._change_pass(new_password=new_password, vms_dates=vms_dates)
                LibvirtManager.Snapshot.create(vms=vms_list, snapshot_name=rc_ver)

            # пост-проверки
            rc1 = Vm._post_expect_exists(vms_list, must_exist=True)
            if rc1 != ExitCodes.OK:
                return rc1

            print("[OK] ВМ(ы) успешно созданы.")
            return ExitCodes.OK

        except Exception as e:
            print(f"[FATAL] create(): {e}")
            return ExitCodes.UNEXPECTED

    def base_create(new_password="1"):
        return Vm.create("/opt/allta_vm/vm/base_vm.json", new_password=new_password)

    def delete(vms: list):
        try:
            rc_names = Vm._require_names_exist(vms)
            if rc_names != ExitCodes.OK:
                return rc_names

            Snapshot.delete_all(vms=vms)
            for vm in vms:
                SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system destroy --domain '{vm}' >/dev/null 2>&1")
                rc_undef = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system undefine --remove-all-storage --domain '{vm}'"
                )
                if rc_undef != 0:
                    print(f"[ERROR] Не удалось удалить '{vm}', rc={rc_undef}")
                    return ExitCodes.ACTION_FAILED

            rc1 = Vm._post_expect_exists(vms, must_exist=False)
            if rc1 != ExitCodes.OK:
                return rc1

            print("[OK] ВМ(ы) удалены.")
            return ExitCodes.OK
        except Exception as e:
            print(f"[FATAL] delete(): {e}")
            return ExitCodes.UNEXPECTED

    def update(info_path: str):
        try:
            vms_dates = load_vms_dates(info_path=info_path)
            vms = list(vms_dates.keys())

            rc_names = Vm._require_names_exist(vms)
            if rc_names != ExitCodes.OK:
                return rc_names

            LibvirtManager.Vm.stop(vms=vms)

            for vm in vms:
                xml_file = f"/vms/{vm}.xml"
                SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system dumpxml --domain '{vm}' > '{xml_file}'")
                new_file = edit_vm(xml_path=xml_file, cpu=vms_dates[vm]['cpu'], ram_mb=vms_dates[vm]['ram'])
                rc_def = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system define '{new_file}' && sudo rm -rf '{new_file}' '{xml_file}'"
                )
                if rc_def != 0:
                    print(f"[ERROR] define для '{vm}' rc={rc_def}")
                    return ExitCodes.ACTION_FAILED

            LibvirtManager.Vm.start(vms=vms)

            print("[OK] ВМ(ы) обновлены и запущены.")
            return ExitCodes.OK
        except Exception as e:
            print(f"[FATAL] update(): {e}")
            return ExitCodes.UNEXPECTED

    def astra_update(info_path: str, rc: str, new_password: str = "1"):
        try:
            vms_dates = load_vms_dates(info_path=info_path)
            vms = list(vms_dates.keys())

            rc_names = Vm._require_names_exist(vms)
            if rc_names != ExitCodes.OK:
                return rc_names

            if rc.startswith("1.7"):
                LibvirtManager.Snapshot.revert(vms=vms, snapshot_name="1.7.5.9_build")
            elif rc.startswith("1.8"):
                LibvirtManager.Snapshot.revert(vms=vms, snapshot_name="1.8.1.6_build")
            else:
                print("Указанная версия не поддерживается")
                return -1

            sleep(90)
            repo = get_repo(rc=rc)
            task = {}
            for name in vms:
                task[name] = {
                    "prepare": {
                        'command': f"echo -e '{repo}' | sudo tee /etc/apt/sources.list",
                        'signal set': 'prepare',
                        'signal get': ''
                    },
                    "astra_update": {
                        'command': "sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive astra-update -A -T -r",
                        'signal set': '',
                        'signal get': ['prepare']
                    },
                }
            Libvirt.execute(commands=task, vms_dates=vms_dates, username="u", password='1')

            Vm._change_pass(vms_dates=vms_dates, new_password=new_password)
            LibvirtManager.Snapshot.create(vms=vms, snapshot_name=rc)

            rc1 = Vm._post_expect_exists(vms, must_exist=True)
            if rc1 != ExitCodes.OK:
                return rc1

            print(f"[OK] astra_update завершён, снапшот '{rc}' создан.")
            return ExitCodes.OK
        except Exception as e:
            print(f"[FATAL] astra_update(): {e}")
            return ExitCodes.UNEXPECTED

    def stop(vms: list):
        try:
            rc_names = Vm._require_names_exist(vms)
            if rc_names != ExitCodes.OK:
                return rc_names

            LibvirtManager.Vm.stop(vms=vms)

            print("[OK] ВМ(ы) остановлены.")
            return ExitCodes.OK
        except Exception as e:
            print(f"[FATAL] stop(): {e}")
            return ExitCodes.UNEXPECTED

    def start(vms: list):
        try:
            rc_names = Vm._require_names_exist(vms)
            if rc_names != ExitCodes.OK:
                return rc_names

            LibvirtManager.Vm.start(vms=vms)

            print("[OK] ВМ(ы) запущены.")
            return ExitCodes.OK
        except Exception as e:
            print(f"[FATAL] start(): {e}")
            return ExitCodes.UNEXPECTED
