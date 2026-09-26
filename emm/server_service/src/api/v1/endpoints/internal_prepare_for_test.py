"""Контракт `prepare-for-test`: вход от testing_service и callback воркера.

Два роутера в одном файле, потому что это две половины одного контракта:

* `router` — s2s-канал под `X-Service-Identity` + `SERVER_INBOUND_SERVICE_API_KEYS`,
  тот же механизм, что у `endpoints/ops.py` и `endpoints/internal_service_reservation.py`.
  Сюда приходит `testing_service`: запускает пайплайн (202) и, если callback
  не доехал, добирает результат GET'ом.
* `worker_router` — обычный internal-канал воркера (bearer worker_bot +
  матрица `entity_permissions`, право `prepare_callback`), тот же, что у
  `acs-snapshot-restore-done` и `prepared`. Сюда приходит
  `server.prepare_for_test` с исходом новых шагов пайплайна.

Почему 202, а не синхронный ответ: пайплайн — restore диска через ACS плюс
авто-prepare плюс провижн пользователя плюс смена ядра плюс ребут. Это часы,
не секунды (одна только верификация подъёма после restore имеет бюджет ~2.5
часа). Синхронного ответа тут быть не может по построению.
"""

from fastapi import APIRouter, Depends, Header, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import (
    SERVICE_RESERVATION_ACS,
    SERVICE_RESERVATION_TESTING,
)
from src.core.exceptions import DomainValidationError, NotFoundError
from src.dependencies.auth import CurrentIdentity, require_internal_caller
from src.dependencies.db import get_db
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
from src.services import internal_service, prepare_for_test as pft_svc
from src.services import stand_setup as stand_setup_svc

# Тот же whitelist, что у брони от имени сервиса: `testing_service` —
# штатный потребитель, `acs` заведён заранее под ретрофит снимков.
_ALLOWED_IDENTITIES = (SERVICE_RESERVATION_TESTING, SERVICE_RESERVATION_ACS)

router = APIRouter(prefix="/internal/servers", include_in_schema=False)
worker_router = APIRouter(prefix="/internal", include_in_schema=False)

_COMMON_RESPONSES = {
    401: {"description": "SERVICE_IDENTITY_REQUIRED / INVALID_SERVICE_TOKEN."},
    403: {"description": "SERVICE_IDENTITY_NOT_ALLOWED."},
    404: {"description": "SERVER_NOT_FOUND."},
}

_TargetDeptHeader = Header(default=None, alias="X-Target-Department-Id")


def _to_status_response(request) -> PrepareForTestStatusResponse:
    return PrepareForTestStatusResponse(
        prepare_request_id=request.id,
        server_id=request.server_id,
        vm_id=request.vm_id,
        vm_snapshot_name=request.vm_snapshot_name,
        correlation_id=request.correlation_id,
        os_version_id=request.os_version_id,
        kernel=request.kernel,
        mode=request.mode,
        preparation=request.preparation,
        skip_pam_fix=request.skip_pam_fix,
        test_username=request.test_username,
        test_account_credential_id=request.test_account_credential_id,
        status=request.status,
        stage=request.stage,
        failed_step=request.failed_step,
        error=request.error,
        created_at=request.created_at,
        completed_at=request.completed_at,
        callback_attempts=request.callback_attempts,
        callback_delivered_at=request.callback_delivered_at,
        callback_last_error=request.callback_last_error,
    )


@router.post(
    "/{server_id}/prepare-for-test",
    response_model=PrepareForTestAcceptedResponse,
    status_code=202,
    responses={
        **_COMMON_RESPONSES,
        409: {
            "description": (
                "SERVER_ALREADY_BUSY / SERVER_DECOMMISSIONED / "
                "SERVER_IS_VMS_HUB / PREPARE_FOR_TEST_ALREADY_RUNNING."
            ),
        },
        503: {"description": "Worker недоступен (WORKER_*)."},
    },
)
async def start_prepare_for_test(
    body: PrepareForTestRequest,
    server_id: str = Path(description="ID сервера-стенда."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> PrepareForTestAcceptedResponse:
    """Запустить подготовку стенда под прогон теста.

    Отвечает сразу: `{prepare_request_id, status}`. Реальный исход придёт
    callback'ом на `{TESTING_SERVICE_URL}/internal/prepare-for-test/
    {prepare_request_id}/completed` — с логином, паролем и приватным ключом
    учётки исполнения теста на успехе, либо с `failed_step` на провале.

    Повтор с тем же `correlation_id` возвращает уже запущенный (или уже
    завершённый) запрос — второй пайплайн не стартует.

    Ядро сверяется с `os_versions.kernels` до любого обращения к стенду:
    несуществующее ядро завершает запрос сразу, `failed_step=kernel_change`.

    Audit: `server.prepare_for_test_requested`.
    """
    if body.target is not None and body.target.type != "server":
        # ВМ готовится через `/internal/vms/{vm_id}/prepare-for-test`.
        raise DomainValidationError(
            error_code="PREPARE_TARGET_MISMATCH",
            message="target.type=vm must be sent to /internal/vms/{vm_id}/prepare-for-test",
        )
    request = await pft_svc.start(
        db,
        server_id=server_id,
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
    return PrepareForTestAcceptedResponse(
        prepare_request_id=request.id, status=request.status,
    )


@router.get(
    "/{server_id}/prepare-for-test/{prepare_request_id}",
    response_model=PrepareForTestStatusResponse,
    responses={
        **_COMMON_RESPONSES,
        404: {"description": "PREPARE_REQUEST_NOT_FOUND / SERVER_NOT_FOUND."},
    },
)
async def get_prepare_for_test_status(
    server_id: str = Path(description="ID сервера-стенда."),
    prepare_request_id: str = Path(description="`prep_<hex>` из 202-ответа."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> PrepareForTestStatusResponse:
    """Состояние запроса — добор результата, если callback не доехал.

    Секретов не отдаёт: пароль и ключ уходят только исходящим callback'ом.
    Здесь — стадия, исход, `failed_step` и счётчик попыток доставки, чтобы
    было видно, где именно застрял пайплайн.
    """
    request = await pft_svc.get_by_id(db, prepare_request_id)
    if request is None or request.server_id != server_id:
        raise NotFoundError(
            error_code="PREPARE_REQUEST_NOT_FOUND",
            message="Prepare-for-test request not found for this server",
        )
    return _to_status_response(request)


@worker_router.post(
    "/servers/{server_id}/prepare-for-test-done",
    response_model=PrepareForTestCallbackResponse,
    responses={
        403: {"description": "PERMISSION_DENIED / TARGET_DEPARTMENT_HEADER_REQUIRED."},
        404: {"description": "SERVER_NOT_FOUND / PREPARE_REQUEST_NOT_FOUND."},
    },
)
async def record_prepare_for_test_done(
    server_id: str,
    body: PrepareForTestCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    x_target_department_id: str | None = _TargetDeptHeader,
) -> PrepareForTestCallbackResponse:
    """Worker сообщает исход новых шагов пайплайна подготовки под тест.

    Шаги воркера: провижн пользователя исполнения теста, смена ядра через
    grub, ребут и верификация подъёма. На успехе server_service отдаёт
    testing_service'у выписанную учётку; на провале — `failed_step` и текст
    ошибки.

    ACS-бронь тут не снимается: её держит testing_service на весь цикл
    (подготовка + сам прогон) и сам же отпускает через
    `/internal/servers/{id}/release-for-service`. Исключение — бронь, взятая
    самим пайплайном на свободном стенде: её на провале возвращаем мы.

    Доступ: `(server, *, prepare_callback)`. Worker_bot роль (seed).

    Audit: `server.prepare_for_test_completed`.
    """
    data = await internal_service.record_prepare_for_test_done(
        db, identity, server_id, body,
        target_department_id=x_target_department_id,
    )
    return PrepareForTestCallbackResponse(**data)


# ── Настройка стенда без restore ──────────────────────


@router.post(
    "/{server_id}/stand-setup",
    response_model=StandSetupAcceptedResponse,
    status_code=202,
    responses={**_COMMON_RESPONSES, 503: {"description": "Worker недоступен (WORKER_*)."}},
)
async def start_stand_setup(
    body: StandSetupRequest,
    server_id: str = Path(description="ID сервера-стенда."),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller(*_ALLOWED_IDENTITIES)),
) -> StandSetupAcceptedResponse:
    """Настроить уже подготовленный стенд: PAM-правка (по профилю), параметры
    ядра и скрипт теста, перезагрузка и ожидание — без restore, без учётки и
    без смены ядра/режима. Бронь сервера держит вызывающий.

    Исход — callback `{TESTING_SERVICE_URL}/internal/stand-setup/{id}/completed`.
    Повтор с тем же `correlation_id` возвращает уже созданный запрос.

    Audit: `server.stand_setup_requested`.
    """
    request = await stand_setup_svc.start(
        db, server_id=server_id, service_name=caller,
        correlation_id=body.correlation_id,
        requested_by_department_id=body.requested_by_department_id,
        test_username=body.test_username,
        stand_setup=body.stand_setup.model_dump(),
        provisioning=body.provisioning.model_dump() if body.provisioning else None,
    )
    return StandSetupAcceptedResponse(stand_setup_request_id=request.id, status=request.status)


@worker_router.post(
    "/servers/{server_id}/stand-setup-done",
    response_model=PrepareForTestCallbackResponse,
    responses={
        403: {"description": "PERMISSION_DENIED."},
        404: {"description": "SERVER_NOT_FOUND / STAND_SETUP_REQUEST_NOT_FOUND."},
    },
)
async def record_stand_setup_done(
    server_id: str,
    body: StandSetupCallbackRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> PrepareForTestCallbackResponse:
    """Worker сообщает исход `server.stand_setup`. Доступ: `(server, *, prepare_callback)`.

    Audit: `server.stand_setup_completed`.
    """
    status, delivered = await stand_setup_svc.record_done(db, identity, server_id, body)
    return PrepareForTestCallbackResponse(status=status, callback_delivered=delivered)
