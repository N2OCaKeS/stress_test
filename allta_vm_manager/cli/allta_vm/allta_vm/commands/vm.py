import os
import sys
import shutil
import logging
import traceback
from time import sleep

from allta import Libvirt, LibvirtManager, SystemCommands
from allta_vm.commands.snapshot import Snapshot
from allta_vm.config import get_repo, load_vms_dates, edit_vm
from allta_vm.libs.exit_code import ExitCodes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

def _log_and_return(where: str, level: int, msg: str, code: int) -> int:
    try:
        code_name = ExitCodes(code).name
    except Exception:
        code_name = str(code)
    logging.log(level, "%s: %s (exit=%s)", where, msg, code_name)
    return code

def _rc_desc(rc: int) -> str:
    try:
        return ExitCodes(rc).name
    except Exception:
        return str(rc)

def _verify_snapshots_exist(vms: list[str], snap_name: str) -> int:
    """
    Проверяет, что у каждой ВМ из vms существует снапшот с именем snap_name.
    Использует: sudo virsh -c qemu:///system snapshot-list --domain <vm> --name
    Возвращает ExitCodes.OK / ExitCodes.VERIFY_FAILED / ExitCodes.UNEXPECTED.
    """
    where = "_verify_snapshots_exist"
    try:
        missing: list[str] = []
        for vm in vms:
            out = SystemCommands.check_output_command(
                "sudo virsh -c qemu:///system snapshot-list --domain '{vm}' --name || true".format(vm=vm)
            )
            names = [line.strip() for line in (out or "").splitlines() if line.strip()]
            # строгая проверка по имени (без игнорирования регистра):
            if snap_name not in names:
                missing.append(vm)

        if missing:
            return _log_and_return(
                where, logging.ERROR,
                "snapshot '{snap}' not found for: {lst}".format(snap=snap_name, lst=", ".join(missing)),
                ExitCodes.VERIFY_FAILED
            )

        logging.info("%s: OK (all VMs have snapshot '%s')", where, snap_name)
        return ExitCodes.OK

    except Exception:
        logging.exception("%s crashed", where)
        return ExitCodes.UNEXPECTED


class Vm:
    @staticmethod
    def _precheck_libvirt_env() -> int:
        where = "_precheck_libvirt_env"
        try:
            required_bins = ["virsh", "virt-install", "qemu-img", "sshpass", "scp"]
            missing = []
            for b in required_bins:
                rc = SystemCommands.cmd_with_returncode(f"command -v {b}")
                if rc != 0:
                    missing.append(b)
            if missing:
                return _log_and_return(where, logging.ERROR,
                                       f"missing binaries: {', '.join(missing)}",
                                       ExitCodes.PRECHECK_MISSING_DEPS)
            rc = SystemCommands.cmd_with_returncode(
                "sudo systemctl start libvirtd && systemctl is-active --quiet libvirtd"
            )
            if rc != 0:
                return _log_and_return(where, logging.ERROR,
                                       "libvirtd inactive or not installed",
                                       ExitCodes.PRECHECK_SERVICE)
            if not os.path.exists("/dev/kvm"):
                return _log_and_return(where, logging.ERROR,
                                       "/dev/kvm not found",
                                       ExitCodes.PRECHECK_KVM)
            if not os.access("/dev/kvm", os.R_OK | os.W_OK):
                return _log_and_return(where, logging.ERROR,
                                       "no R/W access to /dev/kvm",
                                       ExitCodes.PRECHECK_KVM)
            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    @staticmethod
    def _require_names_absent(vms: list[str]) -> int:
        where = "_require_names_absent"
        try:
            for name in vms:
                rc = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system dominfo '{name}'"
                )
                if rc == 0:
                    return _log_and_return(where, logging.ERROR,
                                           f"vm already exists: {name}",
                                           ExitCodes.PRECHECK_NAME_CONFLICT)
            logging.info("%s: OK (%d new names)", where, len(vms))
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    @staticmethod
    def _require_names_exist(vms: list[str]) -> int:
        where = "_require_names_exist"
        try:
            for name in vms:
                rc = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system dominfo '{name}'"
                )
                if rc != 0:
                    return _log_and_return(where, logging.ERROR,
                                           f"vm not found: {name}",
                                           ExitCodes.ACTION_FAILED)
            logging.info("%s: OK (%d names exist)", where, len(vms))
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    @staticmethod
    def _check_space_images_dir(required_gb: int) -> int:
        where = "_check_space_images_dir"
        try:
            path = "/vms"
            if not os.path.exists(path):
                logging.warning("%s: path %s does not exist, creating", where, path)
                os.makedirs(path, exist_ok=True)            
            usage = shutil.disk_usage(path)
            free_gb = usage.free // (1024**3)
            logging.info("%s: free=%d GiB required=%d GiB path=%s",
                         where, free_gb, required_gb, path)
            if free_gb < required_gb:
                return _log_and_return(where, logging.ERROR,
                                       f"insufficient space: have {free_gb} GiB, need {required_gb} GiB at {path}",
                                       ExitCodes.PRECHECK_NO_SPACE)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    @staticmethod
    def _post_expect_exists(vms: list[str], must_exist: bool) -> int:
        where = "_post_expect_exists"
        try:
            bad = []
            for name in vms:
                rc = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system dominfo '{name}'"
                )
                exists = (rc == 0)
                if must_exist and not exists:
                    bad.append(name)
                if not must_exist and exists:
                    bad.append(name)
            if bad:
                return _log_and_return(where, logging.ERROR,
                                       f"unexpected state for: {', '.join(bad)} must_exist={must_exist}",
                                       ExitCodes.VERIFY_FAILED)
            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    @staticmethod
    def _post_expect_running(vms: list[str], must_run: bool) -> int:
        where = "_post_expect_running"
        try:
            bad = []
            for name in vms:
                rc = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system domstate '{name}' | grep -Eiq 'running|работает'"
                )
                running = (rc == 0)
                if must_run and not running:
                    bad.append(name)
                if not must_run and running:
                    bad.append(name)
            if bad:
                return _log_and_return(where, logging.ERROR,
                                       f"unexpected run state for: {', '.join(bad)} must_run={must_run}",
                                       ExitCodes.VERIFY_FAILED)
            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    def _change_pass(vms_dates, new_password):
        where = "_change_pass"
        try:
            passwd = {}
            for vm_name in vms_dates:
                passwd[vm_name] = {
                    'set password': {
                        'command': f"yes \"{new_password}\" | sudo passwd u",
                        'signal set': '',
                        'signal get': ''
                    }
                }
            rc = Libvirt.execute(commands=passwd, vms_dates=vms_dates,
                                 username='u', password='1')
            if rc != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       f"Libvirt.execute failed rc={_rc_desc(rc)}",
                                       ExitCodes.ACTION_FAILED)
            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    def create(info_path, box="vm_station", rc=None, kernel=None, new_password="1"): # work
        where = "create"
        try:
            logging.info("%s: start info_path=%s box=%s rc=%s kernel=%s",
                         where, info_path, box, str(rc), str(kernel))

            rc_pre = Vm._precheck_libvirt_env()
            if rc_pre != ExitCodes.OK:
                return rc_pre

            vms_dates = load_vms_dates(info_path=info_path)
            vms_list = list(vms_dates.keys())
            logging.info("%s: vms=%s", where, ",".join(vms_list))

            rc_names = Vm._require_names_absent(vms_list)
            if rc_names != ExitCodes.OK:
                return rc_names

            required_gb = 100 * len(vms_list) + 115
            rc_space = Vm._check_space_images_dir(required_gb)
            if rc_space != ExitCodes.OK:
                return rc_space

            lv = Libvirt()
            logging.info("%s: build start", where)
            new_vms_dates = lv.build(box=box, rc=rc, vms=vms_list,
                                     vms_dates=vms_dates, kernel=kernel)
            if not new_vms_dates:
                return _log_and_return(where, logging.ERROR,
                                       "Libvirt.build returned empty result",
                                       ExitCodes.ACTION_FAILED)
            logging.info("%s: build done", where)

            group = {'all': vms_list}

            # if box.startswith("1.7") or box.startswith("15GB.1.7") or box.startswith("vm_station1.7"):
            #     rc_list = ["1.7.5.9"]
            # elif box.startswith("1.8") or box.startswith("15GB.1.8") or box.startswith("vm_station1.8"):
            #     rc_list = ["1.8.1.6"]
            # elif box == "vm_station":
            #     rc_list = ["1.7.5.9", "1.8.1.6"]
            rc_list = ["1.7.5.9", "1.8.1.6"]

            for rc_ver in rc_list:
                logging.info("%s: snapshot revert %s_build", where, rc_ver)
                rc_revert = Snapshot.revert(vms=vms_list, snapshot_name=f"{rc_ver}_build")
                if rc_revert != ExitCodes.OK:
                    return rc_revert

                SystemCommands.cmd("sudo rm -rf /root/.ssh/known_hosts ~/.ssh/known_hosts")
                sleep(7)

                scp_prepare = {
                    "g_all": [
                        {
                            'mode': 'push',
                            'path_host': '/opt/allta_vm/vm/provision.sh',
                            'path_vm': '/home/u/env_provision.sh'
                        }
                    ],
                }
                logging.info("%s: scp provision.sh -> /home/u/env_provision.sh", where)
                rc_scp = Libvirt.scp(
                    scp_settings=scp_prepare,
                    vms_dates=new_vms_dates,
                    vms_groups=group,
                    username='u',
                    password='1'
                )
                if rc_scp != ExitCodes.OK:
                    return _log_and_return(where, logging.ERROR,
                                           f"Libvirt.scp failed rc={_rc_desc(rc_scp)}",
                                           ExitCodes.ACTION_FAILED)

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
                                f"sudo su -c '/home/u/env_provision.sh {vms_dates[vm_name]['ip_bridge']}' &&"
                                f"sudo rm /home/u/env_provision.sh"
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
                logging.info("%s: remote prepare+reboot", where)
                rc_exec = Libvirt.execute(
                    commands=prepare,
                    vms_dates=new_vms_dates,
                    vms_groups=group,
                    username='u',
                    password='1'
                )
                if rc_exec != ExitCodes.OK:
                    return _log_and_return(where, logging.ERROR,
                                           f"Libvirt.execute failed rc={_rc_desc(rc_exec)} during prepare",
                                           ExitCodes.ACTION_FAILED)

                logging.info("%s: wait for reboot", where)
                sleep(90)

                for vm in vms_list:
                    logging.info("%s: network.sh %s", where, vm)
                    rc_net = SystemCommands.cmd_with_returncode(f"/opt/allta_vm/vm/network.sh {vm}")
                    if rc_net != 0:
                        return _log_and_return(where, logging.ERROR,
                                               f"network script failed vm={vm} rc={rc_net}",
                                               ExitCodes.ACTION_FAILED)

                logging.info("%s: snapshot delete %s_build", where, rc_ver)
                rc_del = Snapshot.delete(vms=vms_list, snapshot_name=f"{rc_ver}_build")
                if rc_del != ExitCodes.OK:
                    return rc_del

                logging.info("%s: snapshot create %s_build", where, rc_ver)
                rc_create_build = Snapshot.create(vms=vms_list, snapshot_name=f"{rc_ver}_build")
                if rc_create_build != ExitCodes.OK:
                    return rc_create_build

                logging.info("%s: wait after snapshot", where)
                sleep(90)

                logging.info("%s: change passwords", where)
                if Vm._change_pass(new_password=new_password, vms_dates=vms_dates) != ExitCodes.OK:
                    return _log_and_return(where, logging.ERROR,
                                           "password change failed",
                                           ExitCodes.ACTION_FAILED)

                logging.info("%s: snapshot create %s", where, rc_ver)
                rc_create_rc = Snapshot.create(vms=vms_list, snapshot_name=rc_ver)
                if rc_create_rc != ExitCodes.OK:
                    return rc_create_rc
                sleep(90)

            if Vm._post_expect_exists(vms_list, must_exist=True) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "post-check exists failed",
                                       ExitCodes.VERIFY_FAILED)

            logging.info("%s: OK", where)
            return ExitCodes.OK
        

        except Exception:
            logging.error("%s: unhandled exception", where)
            logging.error(traceback.format_exc())
            return ExitCodes.UNEXPECTED

    def base_create(new_password="1"):
        return Vm.create("/opt/allta_vm/vm/base_vm.json", new_password=new_password)

    def delete(vms: list): # work
        where = "delete"
        try:
            logging.info("%s: start vms=%s", where, ",".join(vms))
            if Vm._require_names_exist(vms) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "some VMs do not exist",
                                       ExitCodes.ACTION_FAILED)
            rc_snap = Snapshot.delete_all(vms=vms)
            if rc_snap != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "snapshot.delete_all failed",
                                       ExitCodes.ACTION_FAILED)

            for vm in vms:
                SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system destroy --domain '{vm}'"
                )
                rc_undef = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system undefine --remove-all-storage --domain '{vm}'"
                )
                if rc_undef != 0:
                    return _log_and_return(where, logging.ERROR,
                                           f"undefine failed vm={vm} rc={rc_undef}",
                                           ExitCodes.ACTION_FAILED)

            if Vm._post_expect_exists(vms, must_exist=False) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "post-check not-exist failed",
                                       ExitCodes.VERIFY_FAILED)
            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    def update(info_path: str): # work
        where = "update"
        try:
            logging.info("%s: start info_path=%s", where, info_path)
            vms_dates = load_vms_dates(info_path=info_path)
            vms = list(vms_dates.keys())

            if Vm._require_names_exist(vms) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "some VMs do not exist",
                                       ExitCodes.ACTION_FAILED)

            try:
                LibvirtManager.Vm.stop(vms=vms)
            except Exception as e:
                return _log_and_return(where, logging.ERROR,
                                       f"LibvirtManager.Vm.stop raised: {e}",
                                       ExitCodes.ACTION_FAILED)
            if Vm._post_expect_running(vms, must_run=False) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "vm stop failed (verify)",
                                       ExitCodes.VERIFY_FAILED)

            for vm in vms:
                xml_file = f"/vms/{vm}.xml"
                rc_dump = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system dumpxml --domain '{vm}' > '{xml_file}'"
                )
                if rc_dump != 0:
                    return _log_and_return(where, logging.ERROR,
                                           f"dumpxml failed vm={vm} rc={rc_dump}",
                                           ExitCodes.ACTION_FAILED)

                new_file = edit_vm(
                    xml_path=xml_file,
                    cpu=vms_dates[vm]['cpu'],
                    ram_mb=vms_dates[vm]['ram']
                )

                rc_def = SystemCommands.cmd_with_returncode(
                    f"sudo virsh -c qemu:///system define '{new_file}' && "
                    f"sudo rm -rf '{new_file}' '{xml_file}'"
                )
                if rc_def != 0:
                    return _log_and_return(where, logging.ERROR,
                                           f"define failed vm={vm} rc={rc_def}",
                                           ExitCodes.ACTION_FAILED)

            try:
                LibvirtManager.Vm.start(vms=vms)
            except Exception as e:
                return _log_and_return(where, logging.ERROR,
                                       f"LibvirtManager.Vm.start raised: {e}",
                                       ExitCodes.ACTION_FAILED)

            if Vm._post_expect_running(vms, must_run=True) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "vm start failed (verify)",
                                       ExitCodes.VERIFY_FAILED)

            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    def start(vms: list[str]): # work
        where = "start"
        try:
            logging.info("%s: start vms=%s", where, ",".join(vms))
            if Vm._require_names_exist(vms) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "some VMs do not exist",
                                       ExitCodes.ACTION_FAILED)

            try:
                LibvirtManager.Vm.start(vms=vms)
            except Exception as e:
                return _log_and_return(where, logging.ERROR,
                                       f"LibvirtManager.Vm.start raised: {e}",
                                       ExitCodes.ACTION_FAILED)

            if Vm._post_expect_running(vms, must_run=True) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "post-check running failed",
                                       ExitCodes.VERIFY_FAILED)

            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    def stop(vms: list[str]): # work
        where = "stop"
        try:
            logging.info("%s: stop vms=%s", where, ",".join(vms))
            if Vm._require_names_exist(vms) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "some VMs do not exist",
                                       ExitCodes.ACTION_FAILED)

            try:
                LibvirtManager.Vm.stop(vms=vms)
            except Exception as e:
                return _log_and_return(where, logging.ERROR,
                                       f"LibvirtManager.Vm.stop raised: {e}",
                                       ExitCodes.ACTION_FAILED)

            if Vm._post_expect_running(vms, must_run=False) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "post-check stopped failed",
                                       ExitCodes.VERIFY_FAILED)

            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    def astra_update(info_path: str, snapshot_name: str | None = None, new_password: str = None): # work
        reboot = True
        where = "astra_update"
        try:
            logging.info("%s: start info_path=%s reboot=%s snapshot=%s",
                         where, info_path, reboot, snapshot_name or "")
            vms_dates = load_vms_dates(info_path=info_path)
            vms = list(vms_dates.keys())

            if Vm._require_names_exist(vms) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "some VMs do not exist",
                                       ExitCodes.ACTION_FAILED)

            if snapshot_name:
                if snapshot_name.startswith("1.7"):
                    LibvirtManager.Snapshot.revert(vms=vms, snapshot_name="1.7.5.9_build")
                elif snapshot_name.startswith("1.8"):
                    LibvirtManager.Snapshot.revert(vms=vms, snapshot_name="1.8.1.6_build")

            sleep(90)

            group = {'all': vms}
            repo = get_repo(rc=snapshot_name)

            cmds = {
                "g_all": {
                    'set repo': {
                        'command': f"printf '%s\\n' '{repo}' | sudo tee /etc/apt/sources.list",
                        'signal set': 'repo',
                        'signal get': ''
                    },
                    'update': {
                        'command': "sudo astra-update -A -T -r",
                        'signal set': 'updated',
                        'signal get': ['repo']
                    },
                    'reboot': {
                        'command': "(sleep 2 && sudo reboot) &",
                        'signal set': '',
                        'signal get': ['updated']
                    }
                }
            }

            logging.info("%s: start execute", where)
            rc = Libvirt.execute(commands=cmds, vms_dates=vms_dates,
                                 vms_groups=group, username='u', password='1')
            logging.info("%s: stop execute", where)
            if rc != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       f"Libvirt.execute rc={_rc_desc(rc)} during astra update",
                                       ExitCodes.ACTION_FAILED)

            if reboot:
                logging.info("%s: wait for reboot", where)

            logging.info("%s: change passwords", where)
            if Vm._change_pass(new_password=new_password, vms_dates=vms_dates) != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                       "password change failed",
                                       ExitCodes.ACTION_FAILED)

            logging.info("%s: snapshot create %s", where, snapshot_name)
            rc_create = Snapshot.create(vms=vms, snapshot_name=snapshot_name)
            if rc_create != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR,
                                        f"snapshot.create('{snapshot_name}') failed rc={_rc_desc(rc_create)}",
                                        ExitCodes.ACTION_FAILED)

            rc_verify = _verify_snapshots_exist(vms=vms, snap_name=snapshot_name)
            if rc_verify != ExitCodes.OK:
                return rc_verify

            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED
