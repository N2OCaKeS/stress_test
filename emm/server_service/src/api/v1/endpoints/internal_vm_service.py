"""ВМ-стенды для testing_service: зеркало серверного internal-канала.

Три роутера: `router` — s2s `/internal/vms/{vm_id}/…` (бронь,
connection-info, снимки, prepare-for-test, stand-setup), тот же whitelist,
что у серверных internal-эндпоинтов, тела/ответы — те же поля с `vm_id`;
`worker_router` — callback'и `prepare-for-test-done` и `stand-setup-done`; `settings_router` —
`/settings/vm-test` (шаблоны имени снимка, account_admin).
"""

from fastapi import APIRouter, Depends, Header, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import SERVICE_RESERVATION_ACS, SERVICE_RESERVATION_TESTING
from src.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from src.dependencies.auth import AccountAdminIdentity, CurrentIdentity, require_internal_caller
from src.dependencies.db import get_db
from src.schemas.internal import (
    PreviousHolder,
    ServiceAcquireRequest,
    ServiceBusyStatusRequest,
    VmStatusItem,
)
from src.schemas.prepare_for_test import (
    PrepareForTestAcceptedResponse,
    PrepareForTestCallbackRequest,
    PrepareForTestCallbackResponse,
    PrepareForTestRequest,
    PrepareForTestStatusResponse,
    StandSetupAcceptedResponse,
    StandSetupCallbackRequest,
    StandSetupRequest,
)
from src.schemas.vm_service import (
    VmConnectionInfoResponse,
    VmServiceReservationResponse,
    VmTestSettingsResponse,
    VmTestSettingsUpdate,
    VmTestSnapshotItem,
    VmTestSnapshotListResponse,
)
from src.services import vm_prepare_for_test as vm_pft
from src.services import vm_reservation, vm_test_snapshots
from src.services import prepare_for_test as pft_svc
from src.services import stand_setup as stand_setup_svc

_ALLOWED_IDENTITIES = (SERVICE_RESERVATION_TESTING, SERVICE_RESERVATION_ACS)

router = APIRouter(prefix="/internal/vms", include_in_schema=False)
worker_router = APIRouter(prefix="/internal", include_in_schema=False)
settings_router = APIRouter(prefix="/settings", tags=["system-settings"])

_COMMON_RESPONSES = {
    401: {"description": "SERVICE_IDENTITY_REQUIRED / INVALID_SERVICE_TOKEN."},
    403: {"description": "SERVICE_IDENTITY_NOT_ALLOWED."},
    404: {"description": "VM_NOT_FOUND."},
}
_TargetDeptHeader = Header(default=None, alias="X-Target-Department-Id")


def _to_response(vm, previous_holder: dict | None = None) -> VmServiceReservationResponse:
    view = vm_reservation.reservation_view(vm)
    return VmServiceReservationResponse(
        vm_id=vm.id,
        busy_state=view["busy_state"],
        busy_actor_type=view["busy_actor_type"],
        busy_service_name=view["busy_service_name"],
        busy_note=view["busy_note"],
        busy_since=view["busy_since"],
        previous_holder=PreviousHolder(**previous_holder) if previous_holder else None,
    )


async def vm_status_items(db: AsyncSession, vm_ids: list[str]) -> list[VmStatusItem]:
    """ВМ-часть смешанного batch-status (`/internal/servers/batch-status`)."""
    if not vm_ids:
        return []
    found = await vm_reservation.get_batch_status_for_service(db, vm_ids=vm_ids)
    items: list[VmStatusItem] = []
    for vm_id in vm_ids:
        vm = found.get(vm_id)
        if vm is None:
            items.append(VmStatusItem(vm_id=vm_id, found=False))
            continue
        view = vm_reservation.reservation_view(vm)
        items.append(VmStatusItem(
            vm_id=vm_id, found=True,
            busy_state=view["busy_state"],
            busy_service_name=view["busy_service_name"],
            busy_user_id=view["busy_user_id"],
            busy_actor_type=view["busy_actor_type"],
            busy_note=view["busy_note"],
            ping_reachable=vm.ping_reachable,
            ping_checked_at=vm.ping_checked_at,
        ))
    return items


# ── Бронь ────────────────────────────────────────────────────────────────────


@router.post(
    "/{vm_id}/acquire-for-service",
    response_model=VmServiceReservationResponse,
    responses={**_COMMON_RESPONSES, 409: {"description": "VM_ALREADY_BUSY."}},
)
async def acquire_for_service(
    body: ServiceAcquireRequest,
    vm_id: str = Path(description="ID ВМ."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> VmServiceReservationResponse:
    """Занять свободную ВМ на цикл работы сервиса (CAS; `takeover` — как у серверов).

    Свободна — `status=free`, нет lifecycle-операции и нет сервисной брони.
    `takeover=true` отнимает бронь человека (`status=<login>`) и
    `testing_done`. Audit: `vm.acquired_for_service`, `vm.reservation_taken_over`.
    """
    vm, previous_holder = await vm_reservation.acquire_vm_for_service(
        db, vm_id=vm_id, service_name=caller, busy_state=body.busy_state,
        busy_note=body.busy_note, requested_by_department_id=body.requested_by_department_id,
        takeover=body.takeover,
    )
    return _to_response(vm, previous_holder)


@router.post(
    "/{vm_id}/release-for-service",
    response_model=VmServiceReservationResponse,
    responses={**_COMMON_RESPONSES, 409: {"description": "VM_NOT_BUSY / VM_RESERVED_BY_OTHER."}},
)
async def release_for_service(
    vm_id: str = Path(description="ID ВМ."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> VmServiceReservationResponse:
    """Снять собственную бронь — ВМ в `free`. Audit: `vm.released_for_service`."""
    vm = await vm_reservation.release_vm_for_service(db, vm_id=vm_id, service_name=caller)
    return _to_response(vm)


@router.post(
    "/{vm_id}/release-for-service-as-done",
    response_model=VmServiceReservationResponse,
    responses={**_COMMON_RESPONSES, 409: {"description": "VM_NOT_BUSY / VM_RESERVED_BY_OTHER."}},
)
async def release_for_service_as_done(
    vm_id: str = Path(description="ID ВМ."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> VmServiceReservationResponse:
    """Очередь опустела: бронь → `testing_done`; снимает человек (`POST /vms/{id}/release`)."""
    vm = await vm_reservation.release_vm_for_service_as_done(db, vm_id=vm_id, service_name=caller)
    return _to_response(vm)


@router.post(
    "/{vm_id}/service-status",
    response_model=VmServiceReservationResponse,
    responses={**_COMMON_RESPONSES, 409: {"description": "VM_NOT_BUSY / VM_RESERVED_BY_OTHER."}},
)
async def set_service_status(
    body: ServiceBusyStatusRequest,
    vm_id: str = Path(description="ID ВМ."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> VmServiceReservationResponse:
    """Сменить стадию своей брони (`acs` → `testing`). Audit: `vm.service_status_changed`."""
    vm = await vm_reservation.set_vm_service_status(
        db, vm_id=vm_id, service_name=caller, busy_state=body.busy_state, busy_note=body.busy_note,
    )
    return _to_response(vm)


@router.get(
    "/{vm_id}/connection-info",
    response_model=VmConnectionInfoResponse,
    responses={**_COMMON_RESPONSES, 409: {"description": "VM_NO_IP_ADDRESS."}},
)
async def get_connection_info(
    vm_id: str = Path(description="ID ВМ."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> VmConnectionInfoResponse:
    """IP гостя для SSH testing_worker'а (как `connection-info` сервера)."""
    vm = await vm_reservation.get_vm_for_service(db, vm_id=vm_id)
    if vm.ip_address is None:
        raise ConflictError(error_code="VM_NO_IP_ADDRESS", message="VM has no guest IP address")
    return VmConnectionInfoResponse(vm_id=vm.id, host=str(vm.ip_address))


@router.get(
    "/{vm_id}/snapshots",
    response_model=VmTestSnapshotListResponse,
    responses=_COMMON_RESPONSES,
)
async def list_snapshots_for_service(
    vm_id: str = Path(description="ID ВМ."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> VmTestSnapshotListResponse:
    """Снимки ВМ, пригодные для отката, с версией из имени по шаблонам.

    Замена `acs-snapshots` для ВМ. Имена не по схеме тоже отдаются
    (`version_name=null`).
    """
    vm = await vm_reservation.get_vm_for_service(db, vm_id=vm_id)
    templates = await vm_test_snapshots.get_templates(db)
    items = []
    for snapshot, match in await vm_test_snapshots.list_matched(db, vm, templates):
        items.append(VmTestSnapshotItem(
            snapshot_id=snapshot.id, name=snapshot.name, kind=snapshot.kind,
            os_version=snapshot.os_version, snapshot_mode=snapshot.mode,
            is_current=snapshot.is_current,
            version_name=match.version_name if match else None,
            normalized_version=match.normalized_version if match else None,
            mode=match.mode if match else None,
            template=match.template if match else None,
        ))
    return VmTestSnapshotListResponse(snapshots=items, templates=templates)


# ── prepare-for-test ─────────────────────────────────────────────────────────


@router.post(
    "/{vm_id}/prepare-for-test",
    response_model=PrepareForTestAcceptedResponse,
    status_code=202,
    responses={
        **_COMMON_RESPONSES,
        409: {"description": "VM_ALREADY_BUSY / HUB_UNAVAILABLE / PREPARE_FOR_TEST_ALREADY_RUNNING."},
        422: {"description": "PREPARE_TARGET_MISMATCH."},
        503: {"description": "Worker недоступен (WORKER_*)."},
    },
)
async def start_prepare_for_test(
    body: PrepareForTestRequest,
    vm_id: str = Path(description="ID ВМ-стенда."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> PrepareForTestAcceptedResponse:
    """Подготовить ВМ под тест: откат снимка по шаблонам имени + тот же хвост.

    Снимка нужной версии нет — `failed`, `failed_step=vm_revert`.
    Audit: `server.prepare_for_test_requested` (`target_type=vm`).
    """
    if body.target is not None and (body.target.type != "vm" or body.target.vm_id not in (None, vm_id)):
        raise DomainValidationError(
            error_code="PREPARE_TARGET_MISMATCH",
            message="target in the body does not match the VM in the path",
        )
    request = await vm_pft.start(
        db,
        vm_id=vm_id,
        service_name=caller,
        os_version_id=body.os_version_id,
        kernel=body.kernel,
        mode=body.mode,
        test_username=body.test_username,
        requested_by_department_id=body.requested_by_department_id,
        correlation_id=body.correlation_id,
        test_account_credential_id=body.test_account_credential_id,
        stand_setup=body.stand_setup.model_dump() if body.stand_setup else None,
        provisioning=body.provisioning.model_dump() if body.provisioning else None,
        preparation=body.preparation,
        skip_pam_fix=body.skip_pam_fix,
    )
    return PrepareForTestAcceptedResponse(prepare_request_id=request.id, status=request.status)


@router.get(
    "/{vm_id}/prepare-for-test/{prepare_request_id}",
    response_model=PrepareForTestStatusResponse,
    responses={**_COMMON_RESPONSES, 404: {"description": "PREPARE_REQUEST_NOT_FOUND."}},
)
async def get_prepare_for_test_status(
    vm_id: str = Path(description="ID ВМ-стенда."),
    prepare_request_id: str = Path(description="`prep_<hex>` из 202-ответа."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> PrepareForTestStatusResponse:
    """Состояние запроса — добор результата, если callback не доехал."""
    from src.api.v1.endpoints.internal_prepare_for_test import _to_status_response

    request = await pft_svc.get_by_id(db, prepare_request_id)
    if request is None or request.vm_id != vm_id:
        raise NotFoundError(
            error_code="PREPARE_REQUEST_NOT_FOUND",
            message="Prepare-for-test request not found for this VM",
        )
    return _to_status_response(request)


@worker_router.post(
    "/vms/{vm_id}/prepare-for-test-done",
    response_model=PrepareForTestCallbackResponse,
    responses={
        403: {"description": "PERMISSION_DENIED / TARGET_DEPARTMENT_HEADER_REQUIRED."},
        404: {"description": "VM_NOT_FOUND / PREPARE_REQUEST_NOT_FOUND."},
    },
)
async def record_prepare_for_test_done(
    vm_id: str,
    body: PrepareForTestCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> PrepareForTestCallbackResponse:
    """Worker сообщает исход `vm.prepare_for_test` (все шаги, от отката до ребута).

    Доступ: `(server, *, prepare_callback)`. Audit: `server.prepare_for_test_completed`.
    """
    status, delivered = await vm_pft.record_done(
        db, identity, vm_id, body, target_department_id=x_target_department_id,
    )
    return PrepareForTestCallbackResponse(status=status, callback_delivered=delivered)


# ── Настройка стенда без отката ─────────────────────────────────────────────


@router.post(
    "/{vm_id}/stand-setup",
    response_model=StandSetupAcceptedResponse,
    status_code=202,
    responses={
        **_COMMON_RESPONSES,
        409: {"description": "VM_NOT_BUSY / VM_RESERVED_BY_OTHER / HUB_UNAVAILABLE / VM_NO_IP_ADDRESS."},
        503: {"description": "Worker недоступен (WORKER_*)."},
    },
)
async def start_stand_setup(
    body: StandSetupRequest,
    vm_id: str = Path(description="ID ВМ-стенда."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> StandSetupAcceptedResponse:
    """Настроить подготовленную ВМ между ступенями теста: PAM-правка (по
    профилю), параметры ядра и скрипт теста, перезагрузка — без отката
    снимка, учётки и смены ядра/режима. ВМ должна быть под бронью вызывающего.

    Исход — callback `{TESTING_SERVICE_URL}/internal/stand-setup/{id}/completed`,
    как у сервера. Повтор с тем же `correlation_id` возвращает уже созданный запрос.

    Audit: `server.stand_setup_requested` (`target_type=vm`).
    """
    request = await stand_setup_svc.start_vm(
        db, vm_id=vm_id, service_name=caller,
        correlation_id=body.correlation_id,
        requested_by_department_id=body.requested_by_department_id,
        test_username=body.test_username,
        stand_setup=body.stand_setup.model_dump(),
        provisioning=body.provisioning.model_dump() if body.provisioning else None,
    )
    return StandSetupAcceptedResponse(stand_setup_request_id=request.id, status=request.status)


@worker_router.post(
    "/vms/{vm_id}/stand-setup-done",
    response_model=PrepareForTestCallbackResponse,
    responses={
        403: {"description": "PERMISSION_DENIED / TARGET_DEPARTMENT_HEADER_REQUIRED."},
        404: {"description": "VM_NOT_FOUND / STAND_SETUP_REQUEST_NOT_FOUND."},
    },
)
async def record_stand_setup_done(
    vm_id: str,
    body: StandSetupCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> PrepareForTestCallbackResponse:
    """Worker сообщает исход `vm.stand_setup`. Доступ: `(server, *, prepare_callback)`.

    Audit: `server.stand_setup_completed` (`target_type=vm`).
    """
    status, delivered = await stand_setup_svc.record_vm_done(
        db, identity, vm_id, body, target_department_id=x_target_department_id,
    )
    return PrepareForTestCallbackResponse(status=status, callback_delivered=delivered)


# ── Настройка шаблонов имени снимка ──────────────────────────────────────────


@settings_router.get(
    "/vm-test",
    response_model=VmTestSettingsResponse,
    summary="Настройки подготовки ВМ-стендов под тест",
    responses={403: {"description": "ACCOUNT_ADMIN_REQUIRED."}},
)
async def get_vm_test_settings(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmTestSettingsResponse:
    """Шаблоны имени снимка ВМ, по порядку."""
    return VmTestSettingsResponse(snapshot_name_templates=await vm_test_snapshots.get_templates(db))


@settings_router.put(
    "/vm-test",
    response_model=VmTestSettingsResponse,
    summary="Обновить шаблоны имени снимка ВМ",
    responses={
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "VM_SNAPSHOT_TEMPLATE_INVALID."},
    },
)
async def put_vm_test_settings(
    payload: VmTestSettingsUpdate,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> VmTestSettingsResponse:
    """Заменить список шаблонов. Audit: `settings.vm_test_updated`."""
    templates = await vm_test_snapshots.update_templates(
        db, payload.snapshot_name_templates, updated_by=identity.user_id,
    )
    return VmTestSettingsResponse(snapshot_name_templates=templates)
