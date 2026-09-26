"""Настройка стенда без restore.

Шаги 4–10 пайплайна подготовки на уже подготовленном стенде: PAM-правка,
параметры ядра и скрипт теста, перезагрузка. Без restore, учётки, смены
ядра и режима — между ступенями многоступенчатого теста. Бронь держит
вызывающий (testing_service), здесь не трогается.

Сервер: `start` → задача воркера `server.stand_setup` → `record_done`.
ВМ: `start_vm` → `vm.stand_setup` → `record_vm_done`. ВМ без отката
снимка; в гостя входим той же учёткой, что и `vm.prepare_for_test` после
отката на снимок последней подготовки. Callback в testing_service у обоих
один — `POST /internal/stand-setup/{id}/completed`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, ServerStatus
from src.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ServiceUnavailableError
from src.models import Server, ServerStandSetupRequest, Vm
from src.models.server_prepare_for_test import (
    PREPARE_FOR_TEST_FAILED,
    PREPARE_FOR_TEST_IN_PROGRESS,
    PREPARE_FOR_TEST_SUCCEEDED,
)
from src.repositories import server as server_repo
from src.repositories import vm as vm_repo
from src.schemas.identity import IdentityContext
from src.schemas.prepare_for_test import StandSetupCallbackRequest
from src.services import (
    audit_service,
    internal_service,
    permissions,
    testing_client,
    vm_prepare_for_test,
    vm_reservation,
    worker_client,
)
from src.services.prepare_for_test import read_stand_setup_script, store_stand_setup
from src.utils.ids import dispatch_creds_id, stand_setup_request_id

logger = logging.getLogger(__name__)

AUDIT_ACTION_REQUESTED = "server.stand_setup_requested"
AUDIT_ACTION_COMPLETED = "server.stand_setup_completed"
VM_TASK_KIND = "vm.stand_setup"
_ERROR_MAX_LEN = 2048
_GUEST_SSH_PORT = 22


def _audit_target(request: ServerStandSetupRequest) -> dict:
    if request.vm_id is not None:
        return {"target_id": request.vm_id, "target_type": "vm"}
    return {"target_id": request.server_id, "target_type": "server"}


async def get_by_id(db: AsyncSession, request_id: str) -> ServerStandSetupRequest | None:
    return await db.get(ServerStandSetupRequest, request_id)


async def _by_correlation(db: AsyncSession, correlation_id: str) -> ServerStandSetupRequest | None:
    stmt = select(ServerStandSetupRequest).where(ServerStandSetupRequest.correlation_id == correlation_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def start(
    db: AsyncSession,
    *,
    server_id: str,
    service_name: str,
    correlation_id: str,
    requested_by_department_id: str | None,
    test_username: str,
    stand_setup: dict,
    provisioning: dict | None,
) -> ServerStandSetupRequest:
    existing = await _by_correlation(db, correlation_id)
    if existing is not None:
        return existing
    server: Server | None = await server_repo.get_by_id(db, server_id)
    if server is None or (
        requested_by_department_id is not None and requested_by_department_id != server.department_id
    ):
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    if server.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(error_code="SERVER_DECOMMISSIONED", message="Server is decommissioned")

    request = ServerStandSetupRequest(
        id=stand_setup_request_id(),
        server_id=server.id,
        correlation_id=correlation_id,
        requested_by_department_id=requested_by_department_id,
        requested_by_service=service_name,
        test_username=test_username,
        stand_setup={},
        provisioning=provisioning,
        status=PREPARE_FOR_TEST_IN_PROGRESS,
    )
    db.add(request)
    await db.flush()
    store_stand_setup(request, stand_setup)
    await db.commit()
    audit_service.emit(
        AUDIT_ACTION_REQUESTED,
        target_id=server.id, target_type="server", status="success", allowed=True,
        details={
            "stand_setup_request_id": request.id, "correlation_id": correlation_id,
            "service_name": service_name, "department_id": server.department_id,
            "kernel_cmdline_extra": (request.stand_setup or {}).get("kernel_cmdline_extra"),
            "has_script": bool(request.stand_setup_script_encrypted),
        },
    )

    script = read_stand_setup_script(request)
    script_key = None
    payload = {
        "server_id": server.id,
        "stand_setup_request_id": request.id,
        "target_department_id": server.department_id,
        "host": str(server.ip_address),
        "ssh_port": server.ssh_port,
        "is_managed": True,
        "management_user": server.management_user,
        "test_username": test_username,
        "stand_setup": request.stand_setup,
        "provisioning": provisioning,
    }
    try:
        if script:
            script_key = worker_client.dispatch_creds_key(dispatch_creds_id())
            await worker_client.store_dispatch_creds(script_key, {"stand_setup_script": script})
            payload["script_key"] = script_key
        request.task_id = await worker_client.dispatch_task(
            db=db, task_kind="server.stand_setup", target_server_id=server.id,
            payload=payload, created_by=None, request_id=None, max_attempts=1,
        )
        await db.commit()
    except (ConflictError, ServiceUnavailableError, OSError) as exc:
        await db.rollback()
        if script_key:
            await worker_client.delete_dispatch_creds(script_key)
        request = await get_by_id(db, request.id)
        await _finish(db, request, succeeded=False, failed_step="stand_setup",
                      error=f"failed to dispatch stand_setup task: {type(exc).__name__}")
    return request


async def record_done(
    db: AsyncSession, identity: IdentityContext, server_id: str, body: StandSetupCallbackRequest,
) -> tuple[str, bool]:
    try:
        await permissions.require_action(db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK)
    except AuthorizationError:
        audit_service.emit(
            AUDIT_ACTION_COMPLETED, target_id=server_id, target_type="server",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    request = await get_by_id(db, body.stand_setup_request_id)
    if request is None or request.server_id != server_id:
        raise NotFoundError(error_code="STAND_SETUP_REQUEST_NOT_FOUND", message="Stand setup request not found")
    return await _finish(
        db, request, succeeded=body.succeeded, failed_step=body.failed_step, error=body.error,
    )


async def start_vm(
    db: AsyncSession,
    *,
    vm_id: str,
    service_name: str,
    correlation_id: str,
    requested_by_department_id: str | None,
    test_username: str,
    stand_setup: dict,
    provisioning: dict | None,
) -> ServerStandSetupRequest:
    """То же для ВМ-стенда. ВМ обязана быть под сервисной бронью вызывающего:
    настройка идёт между ступенями теста, который её держит."""
    existing = await _by_correlation(db, correlation_id)
    if existing is not None:
        return existing
    vm: Vm | None = await vm_repo.get_by_id(db, vm_id)
    if vm is None or (
        requested_by_department_id is not None and requested_by_department_id != vm.department_id
    ):
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _require_held_by(vm, service_name)
    hub = await server_repo.get_by_id(db, vm.hub_server_id)
    if hub is None or hub.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="HUB_UNAVAILABLE", message="Hub server is unavailable (missing or decommissioned)",
        )
    if vm.ip_address is None:
        raise ConflictError(error_code="VM_NO_IP_ADDRESS", message="VM has no guest IP address")

    request = ServerStandSetupRequest(
        id=stand_setup_request_id(),
        server_id=None,
        vm_id=vm.id,
        correlation_id=correlation_id,
        requested_by_department_id=requested_by_department_id,
        requested_by_service=service_name,
        test_username=test_username,
        stand_setup={},
        provisioning=provisioning,
        status=PREPARE_FOR_TEST_IN_PROGRESS,
    )
    db.add(request)
    await db.flush()
    store_stand_setup(request, stand_setup)
    await db.commit()
    audit_service.emit(
        AUDIT_ACTION_REQUESTED,
        target_id=vm.id, target_type="vm", status="success", allowed=True,
        details={
            "stand_setup_request_id": request.id, "correlation_id": correlation_id,
            "service_name": service_name, "department_id": vm.department_id,
            "kernel_cmdline_extra": (request.stand_setup or {}).get("kernel_cmdline_extra"),
            "has_script": bool(request.stand_setup_script_encrypted),
            "target_type": "vm",
        },
    )

    stash_key = None
    try:
        snapshot = await vm_prepare_for_test.last_prepared_snapshot(db, vm)
        stash = await vm_prepare_for_test.guest_login(db, vm, snapshot)
        script = read_stand_setup_script(request)
        if script:
            stash["stand_setup_script"] = script
        stash_key = worker_client.dispatch_creds_key(dispatch_creds_id())
        await worker_client.store_dispatch_creds(stash_key, stash)
        payload = {
            "vm_id": vm.id,
            "stand_setup_request_id": request.id,
            "target_department_id": vm.department_id,
            "guest_ip": str(vm.ip_address),
            "guest_ssh_port": _GUEST_SSH_PORT,
            "test_username": test_username,
            "stand_setup": request.stand_setup,
            "provisioning": provisioning,
            "stash_key": stash_key,
        }
        request.task_id = await worker_client.dispatch_task(
            db=db, task_kind=VM_TASK_KIND, target_server_id=hub.id, target_resource_id=vm.id,
            payload=payload, created_by=None, request_id=None, max_attempts=1,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — Redis/секреты/воркер: любой сбой → failed + callback
        await db.rollback()
        if stash_key:
            await worker_client.delete_dispatch_creds(stash_key)
        request = await get_by_id(db, request.id)
        await _finish(db, request, succeeded=False, failed_step="stand_setup",
                      error=f"failed to dispatch vm stand_setup task: {type(exc).__name__}")
    return request


def _require_held_by(vm: Vm, service_name: str) -> None:
    """409, если ВМ не под сервисной бронью `service_name`."""
    if vm_reservation.service_holds(vm, service_name):
        return
    reason = "not_busy" if not vm.service_busy_state else "reservation_held_by_other"
    audit_service.emit(
        AUDIT_ACTION_REQUESTED, target_id=vm.id, target_type="vm",
        status="denied", allowed=False,
        details={"reason": reason, "service_name": service_name, "department_id": vm.department_id},
    )
    if not vm.service_busy_state:
        raise ConflictError(error_code="VM_NOT_BUSY", message="VM has no service reservation")
    raise ConflictError(
        error_code="VM_RESERVED_BY_OTHER",
        message="VM reservation is held by another actor",
        details={"busy_service_name": vm.busy_service_name},
    )


async def record_vm_done(
    db: AsyncSession, identity: IdentityContext, vm_id: str, body: StandSetupCallbackRequest,
    target_department_id: str | None = None,
) -> tuple[str, bool]:
    """Callback воркера `vm.stand_setup`; право и сверка отдела — как у `vm.prepare_for_test`."""
    try:
        await permissions.require_action(db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK)
    except AuthorizationError:
        audit_service.emit(
            AUDIT_ACTION_COMPLETED, target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    request = await get_by_id(db, body.stand_setup_request_id)
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None or request is None or request.vm_id != vm_id:
        raise NotFoundError(error_code="STAND_SETUP_REQUEST_NOT_FOUND", message="Stand setup request not found")
    internal_service._check_target_department(
        audit_action=AUDIT_ACTION_COMPLETED,
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )
    return await _finish(
        db, request, succeeded=body.succeeded, failed_step=body.failed_step, error=body.error,
    )


async def _finish(
    db: AsyncSession, request: ServerStandSetupRequest, *,
    succeeded: bool, failed_step: str | None, error: str | None,
) -> tuple[str, bool]:
    """Записать исход (идемпотентно) и отправить callback в testing_service."""
    if request.status == PREPARE_FOR_TEST_IN_PROGRESS:
        request.status = PREPARE_FOR_TEST_SUCCEEDED if succeeded else PREPARE_FOR_TEST_FAILED
        request.failed_step = None if succeeded else (failed_step or "stand_setup")
        request.error = (error or "")[:_ERROR_MAX_LEN] or None
        request.completed_at = datetime.now(timezone.utc)
        await db.commit()
        audit_service.emit(
            AUDIT_ACTION_COMPLETED, **_audit_target(request),
            status="success" if succeeded else "failure", allowed=True,
            details={
                "stand_setup_request_id": request.id, "correlation_id": request.correlation_id,
                "succeeded": succeeded, "failed_step": request.failed_step,
            },
        )
    body: dict = {"correlation_id": request.correlation_id, "succeeded": request.status == PREPARE_FOR_TEST_SUCCEEDED}
    if request.status == PREPARE_FOR_TEST_FAILED:
        body["failed_step"] = request.failed_step
        body["error"] = request.error
    delivered, attempts, last_error = await testing_client.send_stand_setup_completed(request.id, body)
    request.callback_attempts = (request.callback_attempts or 0) + attempts
    request.callback_last_error = last_error
    if delivered:
        request.callback_delivered_at = datetime.now(timezone.utc)
    await db.commit()
    return request.status, delivered
