import logging
from time import sleep

from allta import LibvirtManager, SystemCommands
from allta_vm.libs.exit_code import ExitCodes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
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

def _snap_exists(vm: str, snap: str) -> bool:
    rc = SystemCommands.cmd_with_returncode(
        f"sudo virsh -c qemu:///system snapshot-info --domain '{vm}' "
        f"--snapshotname '{snap}' >/dev/null 2>&1"
    )
    return rc == 0

def _current_snapshot_name(vm: str) -> tuple[int, str]:
    """
    Возвращает (rc, name). rc==0 если команда отработала, name — имя текущего снапшота (может быть пусто).
    Используем --name, чтобы получить только имя без локализации.
    """
    rc = SystemCommands.cmd_with_returncode(
        f"sudo virsh -c qemu:///system snapshot-current --domain '{vm}' --name >/tmp/cur_snap 2>/tmp/cur_snap_err"
    )
    name_out = SystemCommands.check_output_command("cat /tmp/cur_snap || true").strip()
    # подчистим временные файлы на всякий случай (не критично, но аккуратно)
    SystemCommands.cmd("rm -f /tmp/cur_snap /tmp/cur_snap_err")
    return rc, name_out

class Snapshot:
    @staticmethod
    def create(vms: list, snapshot_name: str) -> int:
        where = "snapshot.create"
        try:
            # precheck: snapshot must NOT exist
            for vm in vms:
                if _snap_exists(vm, snapshot_name):
                    return _log_and_return(
                        where, logging.ERROR,
                        f"snapshot already exists for vm={vm}: {snapshot_name}",
                        ExitCodes.PRECHECK_NAME_CONFLICT
                    )

            # вызываем LibvirtManager, rc игнорируем — дальше верификация
            try:
                LibvirtManager.Snapshot.create(vms=vms, snapshot_name=snapshot_name)
            except Exception as e:
                return _log_and_return(
                    where, logging.ERROR,
                    f"LibvirtManager.Snapshot.create raised: {e}",
                    ExitCodes.ACTION_FAILED
                )

            # verify: snapshot must exist for each VM
            for vm in vms:
                if not _snap_exists(vm, snapshot_name):
                    return _log_and_return(
                        where, logging.ERROR,
                        f"verify failed: snapshot not found after create vm={vm} name={snapshot_name}",
                        ExitCodes.VERIFY_FAILED
                    )

            logging.info("%s: OK name=%s vms=%s", where, snapshot_name, ",".join(vms))
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    @staticmethod
    def delete(vms: list, snapshot_name: str) -> int:
        where = "snapshot.delete"
        try:
            # precheck: snapshot MUST exist
            for vm in vms:
                if not _snap_exists(vm, snapshot_name):
                    return _log_and_return(
                        where, logging.ERROR,
                        f"snapshot not found for vm={vm}: {snapshot_name}",
                        ExitCodes.ACTION_FAILED
                    )

            try:
                LibvirtManager.Snapshot.delete(vms=vms, snapshot_name=snapshot_name)
            except Exception as e:
                return _log_and_return(
                    where, logging.ERROR,
                    f"LibvirtManager.Snapshot.delete raised: {e}",
                    ExitCodes.ACTION_FAILED
                )

            # verify: snapshot MUST be gone
            for vm in vms:
                if _snap_exists(vm, snapshot_name):
                    return _log_and_return(
                        where, logging.ERROR,
                        f"verify failed: snapshot still exists after delete vm={vm} name={snapshot_name}",
                        ExitCodes.VERIFY_FAILED
                    )

            logging.info("%s: OK name=%s vms=%s", where, snapshot_name, ",".join(vms))
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    @staticmethod
    def revert(vms: list, snapshot_name: str) -> int:
        where = "snapshot.revert"
        try:
            # precheck: snapshot MUST exist
            for vm in vms:
                if not _snap_exists(vm, snapshot_name):
                    return _log_and_return(
                        where, logging.ERROR,
                        f"snapshot not found for vm={vm}: {snapshot_name}",
                        ExitCodes.ACTION_FAILED
                    )

            # выполняем revert (не доверяем rc, дальше проверим)
            try:
                LibvirtManager.Snapshot.revert(vms=vms, snapshot_name=snapshot_name)
            except Exception as e:
                return _log_and_return(
                    where, logging.ERROR,
                    f"LibvirtManager.Snapshot.revert raised: {e}",
                    ExitCodes.ACTION_FAILED
                )

            # verify с ретраями: дождаться, что snapshot-current стал нужным
            for vm in vms:
                ok = False
                last_name = ""
                for _ in range(30):  # ~60 сек @ sleep(2)
                    rc, cur = _current_snapshot_name(vm)
                    last_name = cur
                    if rc == 0 and cur == snapshot_name:
                        ok = True
                        break
                    sleep(2)
                if not ok:
                    return _log_and_return(
                        where, logging.ERROR,
                        f"verify failed: snapshot is not current after revert vm(s)={vm} "
                        f"name={snapshot_name} current={last_name or '<empty>'}",
                        ExitCodes.VERIFY_FAILED
                    )

            logging.info("%s: OK name=%s vms=%s", where, snapshot_name, ",".join(vms))
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED

    @staticmethod
    def delete_all(vms: list) -> int:
        where = "snapshot.delete_all"
        try:
            for vm in vms:
                while True:
                    rc, out = SystemCommands.check_output_command_with_returncode(
                        f"virsh snapshot-list {vm} --name --all"
                    )

                    if rc != 0:
                        return _log_and_return(
                            where, logging.ERROR,
                            f"virsh snapshot-list failed vm={vm}: {out}",
                            ExitCodes.ACTION_FAILED
                        )

                    snaps = []
                    for line in out.splitlines():
                        s = line.strip()
                        if s:
                            snaps.append(s)

                    if not snaps:
                        logging.info("%s: no snapshots vm=%s", where, vm)
                        break

                    deleted_any = False

                    for snap in reversed(snaps):
                        rc_del, out_del = SystemCommands.check_output_command_with_returncode(
                            f"virsh snapshot-delete {vm} {snap}"
                        )
                        if rc_del == 0:
                            logging.info("%s: deleted vm=%s snap=%s", where, vm, snap)
                            deleted_any = True
                            continue

                        rc_meta, out_meta = SystemCommands.check_output_command_with_returncode(
                            f"virsh snapshot-delete {vm} {snap} --metadata"
                        )
                        if rc_meta == 0:
                            logging.info("%s: deleted metadata vm=%s snap=%s", where, vm, snap)
                            deleted_any = True
                        else:
                            logging.warning(
                                "%s: delete failed vm=%s snap=%s: %s",
                                where, vm, snap, out_del or out_meta
                            )

                    if not deleted_any:
                        return _log_and_return(
                            where, logging.ERROR,
                            f"cannot delete snapshots for vm={vm}; still present: {', '.join(snaps)}",
                            ExitCodes.ACTION_FAILED
                        )

            logging.info("%s: OK vms=%s", where, ",".join(vms))
            return ExitCodes.OK

        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED
