"""`prepare-for-test` для ВМ-стенда.

Отличие от физического стенда (`services/prepare_for_test.py`) — только
первый шаг: вместо ACS restore откат снимка ВМ на hub'е. Дальше тот же
хвост (`prepare` → `user_provision` → ... → `reboot_verify`), тем же кодом
воркера (`run_setup_pipeline`). Всё одной задачей `vm.prepare_for_test` —
откат занимает секунды, отдельная цепочка restore→callback→prepare не нужна.

Снимок ищется по шаблонам имени (`vm_test_snapshots`); не нашёлся —
`failed_step=vm_revert`, `VM_SNAPSHOT_NOT_FOUND`.

Вход в гостя после отката — не `server.prepare` (он сломал бы `is_managed`
при следующем откате), а по очереди: креды снимка → управляющие креды ВМ →
базовая учётка образа (`u`/`1`).

Тестовая учётка отдела обязательна для ВМ. Бронь — как у сервера: держит
вызывающий, свободную ВМ берём сами и снимаем на провале.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import (
    AppException,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.models import Vm, VmSnapshot
from src.models.server_prepare_for_test import (
    PREPARATION_FULL,
    PREPARE_FOR_TEST_IN_PROGRESS,
    PREPARE_FOR_TEST_SUCCEEDED,
    STEP_KERNEL_CHANGE,
    STEP_USER_PROVISION,
    STEP_VM_REVERT,
    ServerPrepareForTestRequest,
)
from src.repositories import os_version as osv_repo
from src.repositories import server as server_repo
from src.repositories import vm as vm_repo
from src.schemas.identity import IdentityContext
from src.schemas.prepare_for_test import PrepareForTestCallbackRequest
from src.services import (
    audit_service,
    management_user_config,
    permissions,
    secret_client,
    secrets_service,
    vm_reservation,
    vm_test_snapshots,
    worker_client,
)
from src.services import internal_service
from src.services import prepare_for_test as pft
from src.services import vm as vm_svc
from src.utils.ids import dispatch_creds_id, prepare_for_test_request_id

logger = logging.getLogger(__name__)

TASK_KIND = "vm.prepare_for_test"
_GUEST_SSH_PORT = 22


async def get_active_request(db: AsyncSession, vm_id: str) -> ServerPrepareForTestRequest | None:
    """Незавершённый запрос по этой ВМ (не больше одного — индекс на БД)."""
    stmt = select(ServerPrepareForTestRequest).where(
        ServerPrepareForTestRequest.vm_id == vm_id,
        ServerPrepareForTestRequest.status == PREPARE_FOR_TEST_IN_PROGRESS,
    )
    return (await db.execute(stmt)).scalars().first()


async def last_prepared_snapshot(db: AsyncSession, vm: Vm) -> VmSnapshot | None:
    """Снимок, на который ВМ откатила последняя успешная подготовка под тест.

    Нужен «настройке без отката»: в госте сейчас учётки этого снимка. Нет
    такой подготовки или снимок удалён — текущий снимок ВМ (`is_current`).
    """
    name = (await db.execute(
        select(ServerPrepareForTestRequest.vm_snapshot_name)
        .where(
            ServerPrepareForTestRequest.vm_id == vm.id,
            ServerPrepareForTestRequest.status == PREPARE_FOR_TEST_SUCCEEDED,
            ServerPrepareForTestRequest.vm_snapshot_name.is_not(None),
        )
        .order_by(ServerPrepareForTestRequest.completed_at.desc().nulls_last())
        .limit(1)
    )).scalar_one_or_none()
    stmt = select(VmSnapshot).where(VmSnapshot.vm_id == vm.id)
    if name is not None:
        snapshot = (await db.execute(stmt.where(VmSnapshot.name == name).limit(1))).scalar_one_or_none()
        if snapshot is not None:
            return snapshot
    return (await db.execute(stmt.where(VmSnapshot.is_current.is_(True)).limit(1))).scalar_one_or_none()


async def guest_login(db: AsyncSession, vm: Vm, snapshot: VmSnapshot | None) -> dict:
    """Учётка входа в гостя после отката на `snapshot` (см. module docstring)."""
    if snapshot is not None and snapshot.mgmt_user and snapshot.mgmt_ssh_private_key_encrypted:
        return {
            "guest_login_user": snapshot.mgmt_user,
            "guest_private_key": secrets_service.decrypt(
                snapshot.mgmt_ssh_private_key_encrypted,
                aad=secrets_service.aad_for_vm_snapshot_ssh_key(snapshot.id),
            ),
        }
    if snapshot is not None and snapshot.mgmt_user and snapshot.mgmt_password_encrypted:
        return {
            "guest_login_user": snapshot.mgmt_user,
            "guest_password": secrets_service.decrypt(
                snapshot.mgmt_password_encrypted,
                aad=secrets_service.aad_for_vm_snapshot_password(snapshot.id),
            ),
        }
    managed = vm_svc._decrypt_vm_mgmt_creds(vm)
    if managed is not None:
        user = managed["management_user"] or (await management_user_config.get_config(db)).login
        return {"guest_login_user": user, "guest_private_key": managed["private_key"]}
    settings = get_settings()
    return {
        "guest_login_user": settings.vm_image_default_user,
        "guest_password": settings.vm_image_default_password,
    }


async def start(
    db: AsyncSession,
    *,
    vm_id: str,
    service_name: str,
    os_version_id: str,
    kernel: str,
    mode: str,
    test_username: str,
    requested_by_department_id: str | None,
    correlation_id: str,
    test_account_credential_id: str | None = None,
    stand_setup: dict | None = None,
    provisioning: dict | None = None,
    preparation: str = PREPARATION_FULL,
    skip_pam_fix: bool = False,
) -> ServerPrepareForTestRequest:
    """Принять запрос на подготовку ВМ и запустить `vm.prepare_for_test`.

    Идемпотентность, «провал входной валидации = терминальный `failed` с
    callback'ом» и `preparation` — как у `prepare_for_test.start`.
    """
    existing = await pft.get_by_correlation_id(db, correlation_id)
    if existing is not None:
        return existing

    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None or (
        requested_by_department_id is not None and requested_by_department_id != vm.department_id
    ):
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE",
            message="Hub server is unavailable (missing or decommissioned)",
        )
    active = await get_active_request(db, vm_id)
    if active is not None:
        raise ConflictError(
            error_code="PREPARE_FOR_TEST_ALREADY_RUNNING",
            message="Another prepare-for-test pipeline is already running for this VM",
            details={"prepare_request_id": active.id},
        )

    request = ServerPrepareForTestRequest(
        id=prepare_for_test_request_id(),
        server_id=None,
        vm_id=vm.id,
        correlation_id=correlation_id,
        os_version_id=os_version_id,
        kernel=kernel,
        mode=mode,
        preparation=preparation,
        skip_pam_fix=skip_pam_fix,
        test_username=test_username,
        test_account_credential_id=test_account_credential_id,
        requested_by_department_id=requested_by_department_id,
        requested_by_service=service_name,
        status=PREPARE_FOR_TEST_IN_PROGRESS,
        stage=STEP_VM_REVERT,
        provisioning=provisioning,
    )
    db.add(request)
    await db.flush()
    pft.store_stand_setup(request, stand_setup if preparation == PREPARATION_FULL else None)

    audit_service.emit(
        pft.AUDIT_ACTION_REQUESTED,
        target_id=vm.id, target_type="vm",
        status="success", allowed=True,
        details={
            "prepare_request_id": request.id,
            "correlation_id": correlation_id,
            "os_version_id": os_version_id,
            "kernel": kernel,
            "mode": mode,
            "preparation": preparation,
            "skip_pam_fix": skip_pam_fix,
            "test_account_credential_id": test_account_credential_id,
            "service_name": service_name,
            "department_id": vm.department_id,
            "target_type": "vm",
        },
    )

    async def _fail(step: str, error: str) -> ServerPrepareForTestRequest:
        await db.commit()
        await pft._finish_failed(db, request, step, error)
        return request

    os_version = await osv_repo.get_by_id(db, os_version_id)
    if os_version is None:
        return await _fail(STEP_VM_REVERT, f"OS version {os_version_id} is not in the catalog")
    if kernel not in (os_version.kernels or []):
        return await _fail(STEP_KERNEL_CHANGE, f"kernel {kernel!r} is not listed for OS version {os_version.name}")
    if not test_account_credential_id:
        return await _fail(
            STEP_USER_PROVISION,
            "TEST_ACCOUNT_NOT_CONFIGURED: VM stands require the department test account",
        )
    try:
        await secret_client.check_test_account_credential(test_account_credential_id, vm.department_id)
    except AppException as exc:
        return await _fail(STEP_USER_PROVISION, f"{exc.error_code}: {exc.message}")
    if vm.ip_address is None:
        # Гость должен быть доступен по своему IP и воркеру, и testing_worker'у.
        return await _fail(STEP_VM_REVERT, "VM_NO_IP_ADDRESS: VM has no guest IP address (bridge network with a static IP is required)")

    templates = await vm_test_snapshots.get_templates(db)
    snapshot = await vm_test_snapshots.find_snapshot(db, vm, os_version.name, mode, templates)
    if snapshot is None:
        searched = vm_test_snapshots.render_names(vm, templates, os_version.name, mode)
        return await _fail(
            STEP_VM_REVERT,
            f"VM_SNAPSHOT_NOT_FOUND: no snapshot of VM {vm.name} for version {os_version.name} "
            f"(searched: {', '.join(searched)})",
        )
    request.vm_snapshot_name = snapshot.name

    acquired = await vm_reservation.hold_for_prepare(
        db, vm, service_name=service_name,
        busy_note=f"VM|revert|{os_version.name}|{kernel}",
    )
    request.reservation_acquired = acquired
    await db.commit()

    try:
        account = await secret_client.reveal_test_account(test_account_credential_id, vm.department_id)
        guest = await guest_login(db, vm, snapshot)
    except AppException as exc:
        await pft._finish_failed(db, request, STEP_USER_PROVISION, f"{exc.error_code}: {exc.message}")
        return request

    stash_key = worker_client.dispatch_creds_key(dispatch_creds_id())
    stash = {
        "test_username": account["username"],
        "test_password": account["password"],
        "test_ssh_public_key": account["ssh_public_key"],
        **guest,
    }
    script = pft.read_stand_setup_script(request)
    if script:
        stash["stand_setup_script"] = script
    try:
        await worker_client.store_dispatch_creds(stash_key, stash)
    except Exception as exc:  # noqa: BLE001 — любой runtime-фейл Redis
        await worker_client.delete_dispatch_creds(stash_key)
        await pft._finish_failed(
            db, request, STEP_VM_REVERT, f"failed to stash test credentials: {type(exc).__name__}",
        )
        return request

    payload = {
        **vm_svc._hub_payload(hub),
        "vm_id": vm.id,
        "vm_name": vm.name,
        "snapshot_id": snapshot.id,
        "snapshot_name": snapshot.name,
        "guest_ip": str(vm.ip_address),
        "guest_ssh_port": _GUEST_SSH_PORT,
        "prepare_request_id": request.id,
        "kernel": kernel,
        "mode": mode,
        "test_creds_key": stash_key,
    }
    pft.apply_preparation(payload, request)
    if request.stand_setup is not None:
        payload["stand_setup"] = request.stand_setup
    if request.provisioning is not None:
        payload["provisioning"] = request.provisioning
    try:
        task_id = await worker_client.dispatch_task(
            db=db,
            task_kind=TASK_KIND,
            target_server_id=hub.id,
            target_resource_id=vm.id,
            payload=payload,
            created_by=None,
            request_id=None,
            max_attempts=1,
        )
        request.restore_task_id = task_id
        await db.commit()
    except (ConflictError, ServiceUnavailableError) as exc:
        await db.rollback()
        await worker_client.delete_dispatch_creds(stash_key)
        request = await pft.get_by_id(db, request.id)
        await pft._finish_failed(db, request, STEP_VM_REVERT, f"failed to dispatch VM revert: {type(exc).__name__}")
    return request


async def record_done(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: PrepareForTestCallbackRequest,
    target_department_id: str | None = None,
) -> tuple[str, bool]:
    """Callback воркера `vm.prepare_for_test` → общий `complete_from_worker`.

    Право — `(server, *, prepare_callback)`, как у остальных VM-callback'ов
    (VM-домен переиспользует серверные гранты worker_bot'а).
    """
    try:
        await permissions.require_action(db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK)
    except AuthorizationError:
        audit_service.emit(
            pft.AUDIT_ACTION_COMPLETED, target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    request = await pft.get_by_id(db, payload.prepare_request_id)
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None or request is None or request.vm_id != vm_id:
        audit_service.emit(
            pft.AUDIT_ACTION_COMPLETED, target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "prepare_request_not_found", "prepare_request_id": payload.prepare_request_id},
        )
        raise NotFoundError(
            error_code="PREPARE_REQUEST_NOT_FOUND",
            message="Prepare-for-test request not found for this VM",
        )
    # Отдел — только по заголовку воркера, как у остальных VM-callback'ов.
    internal_service._check_target_department(
        audit_action=pft.AUDIT_ACTION_COMPLETED,
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )
    return await pft.complete_from_worker(
        db, request, succeeded=payload.succeeded, failed_step=payload.failed_step, error=payload.error,
    )
