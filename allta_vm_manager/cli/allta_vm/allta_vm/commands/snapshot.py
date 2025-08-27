from allta import SystemCommands, LibvirtManager

class SnapExit:
    OK = 0
    PRECHECK_NOT_FOUND = 10
    PRECHECK_ALREADY_EXISTS = 11
    ACTION_FAILED = 20
    VERIFY_FAILED = 30
    UNEXPECTED = 50


class Snapshot:
    # ---------- helpers ----------
    @staticmethod
    def _list(vm: str) -> list[str]:
        """
        Возвращает список имён снимков ВМ.
        Используем только SystemCommands.check_output_command (нельзя менять).
        """
        cmd = f"sudo virsh -c qemu:///system snapshot-list --name --domain '{vm}'"
        out = SystemCommands.check_output_command(cmd) or ""
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines

    @staticmethod
    def _exists(vm: str, snapshot_name: str) -> bool:
        cmd = (
            f"sudo virsh -c qemu:///system snapshot-list --name --domain '{vm}' "
            f"| grep -x '{snapshot_name}' >/dev/null 2>&1"
        )
        rc = SystemCommands.cmd_with_returncode(cmd)
        return rc == 0
    @staticmethod
    def create(vms: list[str], snapshot_name: str) -> int:
        """
        Пред: снимка НЕ существует.
        Пост: снимок появился.
        """
        try:
            # pre
            conflicts = [vm for vm in vms if Snapshot._exists(vm, snapshot_name)]
            if conflicts:
                print(f"[PRECHECK] Уже существуют снимки '{snapshot_name}' для: {', '.join(conflicts)}")
                return SnapExit.PRECHECK_ALREADY_EXISTS
            LibvirtManager.Snapshot.create(vms=vms, snapshot_name=snapshot_name)
            # post
            failed = [vm for vm in vms if not Snapshot._exists(vm, snapshot_name)]
            if failed:
                print(f"[POSTCHECK] Снимок '{snapshot_name}' не появился для: {', '.join(failed)}")
                return SnapExit.VERIFY_FAILED
            print(f"[OK] Созданы снимки '{snapshot_name}' для: {', '.join(vms)}")
            return SnapExit.OK
        except Exception as e:
            print(f"[FATAL] snapshot.create(): {e}")
            return SnapExit.UNEXPECTED

    @staticmethod
    def delete(vms: list[str], snapshot_name: str) -> int:
        """
        Пред: снимок существует.
        Пост: снимок удалён.
        """
        try:
            missing = [vm for vm in vms if not Snapshot._exists(vm, snapshot_name)]
            if missing:
                print(f"[PRECHECK] Не найден снимок '{snapshot_name}' для: {', '.join(missing)}")
                return SnapExit.PRECHECK_NOT_FOUND
            LibvirtManager.Snapshot.delete(vms=vms, snapshot_name=snapshot_name)

            still = [vm for vm in vms if Snapshot._exists(vm, snapshot_name)]
            if still:
                print(f"[POSTCHECK] Снимок '{snapshot_name}' не удалён для: {', '.join(still)}")
                return SnapExit.VERIFY_FAILED
            print(f"[OK] Удалены снимки '{snapshot_name}' для: {', '.join(vms)}")
            return SnapExit.OK

        except Exception as e:
            print(f"[FATAL] snapshot.delete(): {e}")
            return SnapExit.UNEXPECTED

    @staticmethod
    def revert(vms: list[str], snapshot_name: str) -> int:
        """
        Пред: снимок существует.
        Пост: проверяем RC операции (сам снимок остаётся существовать).
        """
        try:
            missing = [vm for vm in vms if not Snapshot._exists(vm, snapshot_name)]
            if missing:
                print(f"[PRECHECK] Не найден снимок '{snapshot_name}' для: {', '.join(missing)}")
                return SnapExit.PRECHECK_NOT_FOUND
            LibvirtManager.Snapshot.revert(vms=vms, snapshot_name=snapshot_name)

            print(f"[OK] Выполнен revert к '{snapshot_name}' для: {', '.join(vms)}")
            return SnapExit.OK

        except Exception as e:
            print(f"[FATAL] snapshot.revert(): {e}")
            return SnapExit.UNEXPECTED

    @staticmethod
    def delete_all(vms: list[str]) -> int:
        """
        Удаление всех снимков для каждой ВМ с пост-проверкой.
        """
        try:
            for vm in vms:
                snaps = Snapshot._list(vm)
                # если список пуст — просто продолжаем
                for snap in snaps:
                    LibvirtManager.Snapshot.delete(vms=[vm], snapshot_name=snap)

                # post: у ВМ не должно остаться ни одного снимка
                left = Snapshot._list(vm)
                if left:
                    print(f"[POSTCHECK] Не все снимки удалены у {vm}: осталось {', '.join(left)}")
                    return SnapExit.VERIFY_FAILED

            print(f"[OK] Все снимки удалены для: {', '.join(vms)}")
            return SnapExit.OK

        except Exception as e:
            print(f"[FATAL] snapshot.delete_all(): {e}")
            return SnapExit.UNEXPECTED
