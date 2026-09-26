"""Подготовка ВМ-стенда под прогон теста (`vm.prepare_for_test`).

Аналог пары `acs.snapshot_restore` → `server.prepare_for_test` для ВМ, одной
задачей: `vm_revert` (destroy → snapshot-revert → start на hub'е, тем же
`revert_domain`, что у `vm.snapshot_revert`) → `prepare` (вход учёткой
снимка + `verify_system_running`) → `user_provision` → тот же
`run_setup_pipeline`, что у физического стенда.

Гость доступен воркеру напрямую по IP (мостовая сеть ВМ), поэтому все шаги
после отката идут обычной SSH-сессией, без прыжка через hub.

Исход — `submit_vm_prepare_for_test_result`, дальше решает server_service.
Задача одноразовая (`max_attempts=1`).

`vm.stand_setup` — настройка уже подготовленной ВМ между ступенями теста:
без отката и учётки, тот же `run_setup_pipeline` без ядра и режима (как
`server.stand_setup`), вход в гостя — той же учёткой из stash.
"""

from __future__ import annotations

import logging

from src.clients.ssh import SshClient, SshError
from src.core.config import get_settings
from src.core.constants import LAST_ERROR_MAX_LEN
from src.main import broker
from src.services import server_service_client, ssh_client
from src.tasks import prepare_for_test as pft
from src.tasks._runner import run_task
from src.tasks._stand_setup_helpers import STEP_STAND_SETUP, parse_provisioning, parse_stand_setup
from src.tasks._vms_helpers import (
    LIBVIRT_SESSION_ENV,
    map_domstate,
    open_hub_session,
    run_hub_cmd,
    validate_ip,
    validate_name,
)
from src.tasks.vms_snapshots import revert_domain
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

STEP_VM_REVERT = "vm_revert"
STEP_PREPARE = "prepare"

# Ни пароль тестовой учётки, ни учётка входа в гостя в audit не идут.
AUDIT_SAFE_FIELDS: set[str] = {
    "vm_id", "prepare_request_id", "snapshot_name", "kernel", "mode", "succeeded",
}


def _guest_creds(stash: dict, guest_ip: str, guest_port: int) -> dict:
    """Креды SSH-сессии в гостя для `ssh_client.build_session`.

    Ключ управляющего пользователя → ключевая сессия (`is_managed`, sudo
    NOPASSWD); пароль (базовая учётка образа, креды снимка) → сессия по паролю,
    sudo получает тот же пароль.
    """
    user = stash.get("guest_login_user")
    if not isinstance(user, str) or not user:
        raise SshError(
            error_code="SSH_TEST_CREDS_MISSING", host=guest_ip,
            message="VM guest login is missing in the prepare-for-test stash",
        )
    base = {"host": guest_ip, "ssh_port": guest_port}
    if stash.get("guest_private_key"):
        return {
            **base, "is_managed": True, "management_user": user,
            "management_private_key": stash["guest_private_key"],
        }
    if stash.get("guest_password"):
        return {**base, "is_managed": False, "login": user, "password": stash["guest_password"]}
    raise SshError(
        error_code="SSH_TEST_CREDS_MISSING", host=guest_ip,
        message="VM guest login has neither a key nor a password",
    )


async def _revert_and_start(ssh: SshClient, vm_name: str, snap: str, host: str) -> str:
    """destroy → revert → start (если после отката домен не запущен)."""
    await ssh.run(f"{LIBVIRT_SESSION_ENV} virsh destroy {vm_name}", sudo=True)
    power_state = await revert_domain(ssh, vm_name, snap, host)
    if power_state != "on":
        await run_hub_cmd(
            ssh, f"virsh start {vm_name}", host,
            "VM_SNAPSHOT_FAILED", f"не удалось запустить ВМ после отката на {snap}",
        )
        _rc, dom_out, _err = await ssh.run(
            f"{LIBVIRT_SESSION_ENV} virsh domstate {vm_name}", sudo=True,
        )
        power_state = map_domstate(dom_out)
    return power_state


async def _report_revert(vm_id: str, snapshot_id: str | None, snap: str, power_state: str, target_dept) -> None:
    """Зеркало снимков и питания в server_service — как у `vm.snapshot_revert`.

    Best-effort: карточка ВМ — справочная, провал отчёта не срывает подготовку.
    """
    entry: dict = {"name": snap, "state": "ready", "is_current": True}
    if snapshot_id is not None:
        entry["snapshot_id"] = snapshot_id
    try:
        await server_service_client.submit_vm_snapshots(vm_id, [entry], target_department_id=target_dept)
        await server_service_client.submit_vm_state(vm_id, target_department_id=target_dept, power_state=power_state)
    except Exception:  # noqa: BLE001
        logger.warning("vm.prepare_for_test: revert report failed vm_id=%s", vm_id, exc_info=True)


@broker.task("vm.prepare_for_test")
async def vm_prepare_for_test(task_id: str) -> None:
    """Откатить ВМ на снимок версии и довести её до «можно гонять тест» (см. module docstring).

    Payload: hub-блок (`server_id`=hub, `host`, `ssh_port`, `is_managed`,
    `management_user`, `target_department_id`), `vm_id`, `vm_name`,
    `snapshot_id`, `snapshot_name`, `guest_ip`, `guest_ssh_port`,
    `prepare_request_id`, `kernel`, `mode`, `skip_mode_switch`, `skip_pam_fix`,
    `test_creds_key`, `stand_setup`, `provisioning`.

    На любой ошибке — callback с `succeeded=False` и именем упавшего шага,
    затем task падает.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        request_id = payload["prepare_request_id"]
        target_dept = payload.get("target_department_id")
        creds_key = payload.get("test_creds_key")
        settings = get_settings()
        step = {"name": STEP_VM_REVERT}
        host_label = str(payload.get("host") or payload.get("server_id") or vm_id)
        mode_warning: str | None = None
        snap = ""
        kernel = ""
        mode = ""

        def _on_step(name: str) -> None:
            step["name"] = name

        try:
            vm_name = validate_name(payload.get("vm_name") or "", host_label, "vm_name")
            snap = validate_name(payload.get("snapshot_name") or "", host_label, "snapshot_name")
            guest_ip = validate_ip(str(payload.get("guest_ip") or ""), host_label).split("/")[0]
            guest_port = int(payload.get("guest_ssh_port") or 22)
            stash = await pft._read_test_creds(creds_key)
            creds = _guest_creds(stash, guest_ip, guest_port)
            kernel = pft._validate_kernel(payload.get("kernel"), guest_ip)
            mode = pft._validate_mode(payload.get("mode"), guest_ip)
            raw_setup = dict(payload.get("stand_setup") or {})
            if stash.get("stand_setup_script"):
                raw_setup["script"] = stash["stand_setup_script"]
            stand_setup = parse_stand_setup(raw_setup)
            provisioning = pft.pipeline_provisioning(payload)
            test_username = str(stash.get("test_username") or "")

            # 1. vm_revert
            session, hub_host = await open_hub_session(payload)
            async with session as ssh:
                power_state = await _revert_and_start(ssh, vm_name, snap, hub_host)
            await _report_revert(vm_id, payload.get("snapshot_id"), snap, power_state, target_dept)
            came_up = await pft._await_reachability(
                guest_ip, ssh_port=guest_port, want_up=True,
                deadline_seconds=settings.acs_reachability_timeout_seconds,
                poll_interval_seconds=settings.acs_reachability_poll_interval_seconds,
            )
            if not came_up:
                raise SshError(
                    error_code="VM_GUEST_UNREACHABLE", host=guest_ip,
                    message=f"VM guest did not become reachable after revert to {snap}",
                )

            # 2. prepare: вход учёткой снимка + готовность системы
            _on_step(STEP_PREPARE)

            def _session() -> SshClient:
                return ssh_client.build_session(creds, vm_id)

            async def _reboot() -> None:
                await pft._reboot_and_wait(creds, vm_id, guest_ip, guest_port, settings)

            await pft.verify_system_running(
                guest_ip, _session, label="vm_prepare_for_test: after revert",
                provisioning=provisioning, reboot=_reboot,
            )

            # 3. user_provision
            _on_step(pft.STEP_USER_PROVISION)
            async with ssh_client.build_session(creds, vm_id) as ssh:
                await ssh.create_user(
                    test_username,
                    new_password=stash.get("test_password"),
                    public_key=stash.get("test_ssh_public_key"),
                    has_sudo=True,
                    force_replace=True,
                )

            # 4–10: тот же хвост, что у физического стенда.
            mode_warning = await pft.run_setup_pipeline(
                creds=creds, server_id=vm_id, host=guest_ip, ssh_port=guest_port,
                settings=settings, provisioning=provisioning, stand_setup=stand_setup,
                test_username=test_username, kernel=kernel,
                mode=pft.pipeline_mode(payload, mode), on_step=_on_step,
            )
        except Exception as exc:
            error_message = redact_error_message(f"{type(exc).__name__}: {exc}")[:LAST_ERROR_MAX_LEN]
            try:
                await server_service_client.submit_vm_prepare_for_test_result(
                    vm_id, request_id, False, target_dept,
                    failed_step=step["name"], error_message=error_message,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "vm_prepare_for_test failed-callback errored vm_id=%s; "
                    "testing_service will not learn the outcome",
                    vm_id, exc_info=True,
                )
            raise
        finally:
            if isinstance(creds_key, str):
                await pft._delete_test_creds(creds_key)

        await server_service_client.submit_vm_prepare_for_test_result(
            vm_id, request_id, True, target_dept, error_message=mode_warning,
        )
        return {
            "vm_id": vm_id,
            "prepare_request_id": request_id,
            "snapshot_name": snap,
            "kernel": kernel,
            "mode": mode,
            "succeeded": True,
        }

    await run_task(
        task_id,
        audit_action="vm.prepare_for_test",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("vm.stand_setup")
async def vm_stand_setup(task_id: str) -> None:
    """Настройка ВМ-стенда без отката: `pam_fix` + `stand_setup` + reboot_verify.

    Payload: `vm_id`, `stand_setup_request_id`, `target_department_id`,
    `guest_ip`, `guest_ssh_port`, `test_username`, `stand_setup`,
    `provisioning`, `stash_key` (учётка входа в гостя и скрипт настройки).
    Исход — callback `submit_vm_stand_setup_result`.
    """
    async def _impl(payload: dict) -> dict:
        vm_id = payload["vm_id"]
        request_id = payload["stand_setup_request_id"]
        target_dept = payload.get("target_department_id")
        stash_key = payload.get("stash_key")
        settings = get_settings()
        step = {"name": STEP_STAND_SETUP}

        def _on_step(name: str) -> None:
            step["name"] = name

        try:
            guest_ip = validate_ip(str(payload.get("guest_ip") or ""), vm_id).split("/")[0]
            guest_port = int(payload.get("guest_ssh_port") or 22)
            stash = await pft._read_stash(stash_key)
            creds = _guest_creds(stash, guest_ip, guest_port)
            raw_setup = dict(payload.get("stand_setup") or {})
            raw_setup["script"] = stash.get("stand_setup_script") or ""
            await pft.run_setup_pipeline(
                creds=creds, server_id=vm_id, host=guest_ip, ssh_port=guest_port,
                settings=settings, provisioning=parse_provisioning(payload.get("provisioning")),
                stand_setup=parse_stand_setup(raw_setup),
                test_username=str(payload.get("test_username") or ""),
                kernel=None, mode=None, on_step=_on_step,
            )
        except Exception as exc:
            error_message = redact_error_message(f"{type(exc).__name__}: {exc}")[:LAST_ERROR_MAX_LEN]
            try:
                await server_service_client.submit_vm_stand_setup_result(
                    vm_id, request_id, False, target_dept,
                    failed_step=step["name"], error_message=error_message,
                )
            except Exception:  # noqa: BLE001
                logger.warning("vm stand_setup failed-callback errored vm_id=%s", vm_id, exc_info=True)
            raise
        finally:
            if isinstance(stash_key, str):
                await pft._delete_test_creds(stash_key)

        await server_service_client.submit_vm_stand_setup_result(vm_id, request_id, True, target_dept)
        return {"vm_id": vm_id, "stand_setup_request_id": request_id, "succeeded": True}

    await run_task(
        task_id,
        audit_action="vm.stand_setup",
        audit_target_type="vm",
        impl=_impl,
        audit_safe_fields={"vm_id", "stand_setup_request_id", "succeeded"},
    )
