"""Пайплайн «подготовить сервер под тест» — оркестрация со стороны server_service.

Что это. `testing_service` не умеет и не должен уметь готовить стенд сам: у
него нет ни mgmt-кред `dbos`, ни bootstrap-пароля версии ОС, ни доступа к ACS.
Он присылает один асинхронный запрос — «дай мне стенд на версии X с ядром Y и
пользователем Z» — и получает 202 с `prepare_request_id`; результат приходит
callback'ом часы спустя.

Как собран пайплайн. Первые две стадии — это **уже существующая** цепочка
ACS-restore → авто-prepare, ничего нового в ней не делается:

    acs.snapshot_restore  →  record_acs_snapshot_restore_done  →
    server.prepare        →  record_server_prepared

Мы к ней подключаемся хуками (`on_restore_failed` / `on_server_prepared`), а
не копируем её: тот же таск, тот же callback, та же bootstrap-логика. Новые
шаги — провижн тестового пользователя, смена ядра, ребут и верификация подъёма
— живут в одном новом таске `server.prepare_for_test`, который стартует ровно
там, где старая цепочка заканчивалась (сервер managed, mgmt-сессия работает).

Стадии и их `failed_step` в callback'е:

    restore        acs.snapshot_restore не смог восстановить диск
    prepare        авто-prepare после reimage не довёл сервер до managed
    user_provision не удалось завести/переустановить пользователя теста
    kernel_change  ядра нет в каталоге РЦ либо grub не переключился
    mode_switch    astra-modeswitch не применился (проверяется get'ом до и после)
    reboot_verify  сервер не поднялся после ребута в отведённое окно

Учётки. Их три, и все три уже существовали до этой фичи — четвёртой не
заводим:

* платформенная `dbos` (mgmt SSH-ключ per-server) — ей делаются restore,
  prepare, смена ядра и ребут;
* bootstrap-пароль версии ОС (`os_version_bootstrap_passwords`) — первый вход
  на свежевосстановленный диск, его резолвит существующая ACS-ветка;
* учётка **исполнения теста** (`server_test_credentials`) — новая для стенда,
  но не новый слой: её выписывает этот пайплайн и отдаёт наружу только в
  callback'е.

Бронь. Если стенд уже забронирован вызывающим сервисом
(`/internal/servers/{id}/acquire-for-service`), мы её не трогаем — отпускает
её тот, кто взял. Если стенд был свободен, бронь берём сами и на провале сами
же возвращаем.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, BusyActorType, BusyState, EntityType, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.models import Server
from src.models.server_prepare_for_test import (
    PREPARE_FOR_TEST_FAILED,
    PREPARE_FOR_TEST_IN_PROGRESS,
    PREPARE_FOR_TEST_SUCCEEDED,
    STEP_KERNEL_CHANGE,
    STEP_PREPARE,
    STEP_REBOOT_VERIFY,
    STEP_RESTORE,
    STEP_USER_PROVISION,
    ServerPrepareForTestRequest,
    ServerTestCredentials,
)
from src.repositories import os_version as osv_repo
from src.repositories import server as server_repo
from src.schemas.identity import IdentityContext
from src.services import (
    audit_service,
    os_version_bootstrap_password as bootstrap_password_svc,
    permissions,
    reservation,
    secrets_service,
    testing_client,
    worker_client,
)
from src.services import server as server_svc
from src.services.server_account import (
    _generate_ssh_keypair,
    _generate_strong_password,
)
from src.utils.ids import (
    dispatch_creds_id,
    prepare_for_test_request_id,
    server_test_credentials_id,
)

logger = logging.getLogger(__name__)

AUDIT_ACTION_REQUESTED = "server.prepare_for_test_requested"
AUDIT_ACTION_COMPLETED = "server.prepare_for_test_completed"

# Заметка занятости на время подготовки — формат из плана миграции:
# `ACS|revert|<rc>|<kernel>`. Действие `revert` единственное, что бывает в
# этом потоке, но поле оставлено явным на случай других ACS-действий позже.
_BUSY_NOTE_ACTION = "revert"

# Ошибку из worker-callback'а режем перед записью: `error` из воркера уже
# отредактирован от секретов, но длина не ограничена.
_ERROR_MAX_LEN = 2048


# ── Чтение ───────────────────────────────────────────────────────────────────


async def get_by_id(
    db: AsyncSession, request_id: str,
) -> ServerPrepareForTestRequest | None:
    """Запрос по его `prep_`-id."""
    stmt = select(ServerPrepareForTestRequest).where(
        ServerPrepareForTestRequest.id == request_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_correlation_id(
    db: AsyncSession, correlation_id: str,
) -> ServerPrepareForTestRequest | None:
    """Запрос по `correlation_id` — ключу идемпотентности testing_service."""
    stmt = select(ServerPrepareForTestRequest).where(
        ServerPrepareForTestRequest.correlation_id == correlation_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_active_request(
    db: AsyncSession, server_id: str,
) -> ServerPrepareForTestRequest | None:
    """Незавершённый запрос по этому серверу (их не может быть больше одного)."""
    stmt = select(ServerPrepareForTestRequest).where(
        ServerPrepareForTestRequest.server_id == server_id,
        ServerPrepareForTestRequest.status == PREPARE_FOR_TEST_IN_PROGRESS,
    )
    return (await db.execute(stmt)).scalars().first()


# ── Учётка исполнения теста ──────────────────────────────────────────────────


async def _issue_test_credentials(
    db: AsyncSession, server: Server, username: str,
) -> dict:
    """Выписать серверу новую учётку исполнения теста. Возвращает plaintext.

    Всегда генерирует свежий материал: restore стирает предыдущего
    пользователя вместе с диском, а держать в БД пароль, которого на боксе
    уже нет, смысла нет. Строка одна на сервер — перевыпуск переписывает её
    на месте, старый секрет не хранится (переходного периода тут нет: на
    боксе в этот момент вообще ничего нет).

    Пароль — общий генератор сервиса (24 символа, shell-безопасный алфавит),
    ключ — Ed25519, тот же `_generate_ssh_keypair`, что у server_account и
    mgmt-кред. Ciphertext'ы — обычный AES-256-GCM конверт под своими AAD.
    """
    password = _generate_strong_password()
    private_pem, public_openssh = _generate_ssh_keypair()

    stmt = select(ServerTestCredentials).where(
        ServerTestCredentials.server_id == server.id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = ServerTestCredentials(
            id=server_test_credentials_id(),
            server_id=server.id,
            username=username,
            password_encrypted="",
            ssh_public_key=public_openssh,
            ssh_private_key_encrypted="",
        )
        db.add(row)
    row.username = username
    row.ssh_public_key = public_openssh
    row.password_encrypted = secrets_service.encrypt(
        password, aad=secrets_service.aad_for_server_test_password(row.id),
    )
    row.ssh_private_key_encrypted = secrets_service.encrypt(
        private_pem, aad=secrets_service.aad_for_server_test_ssh_key(row.id),
    )
    row.rotated_at = datetime.now(timezone.utc)
    await db.flush()
    return {
        "username": username,
        "password": password,
        "ssh_public_key": public_openssh,
        "ssh_private_key": private_pem,
    }


async def reveal_test_credentials(
    db: AsyncSession, identity: IdentityContext, server_id: str, *, reveal: bool,
) -> dict:
    """Карточка учётки исполнения теста для человека (§5.3 плана).

    Гейт — `(server, view_test_credentials)`, единым action'ом на метаданные
    и на сам секрет (без раздельного view/view_password, как у server_account —
    план явно называет это admin-only живой отладкой, а не операционным
    просмотром). Видимость сервера (cross-department 404) переиспользует
    `server_svc.get_server` — тот уже делает `_ensure_visible`, второй раз
    эту логику здесь не пишем.

    `reveal=False` — только метаданные (`exists`/`username`/`ssh_public_key`/
    `rotated_at`), секретные поля `None`. `reveal=True` добавляет
    `password_b64`/`ssh_private_key_b64` и пишет CRITICAL audit
    `server.test_credentials_revealed` — отдельно от обычного `view`-события,
    чтобы SIEM отличал просмотр карточки от реального раскрытия секрета.
    """
    try:
        await permissions.require_resource_action(
            db, identity, EntityType.SERVER, server_id, Action.VIEW_TEST_CREDENTIALS,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.test_credentials_revealed" if reveal else "server.test_credentials_viewed",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    # 404 для non-existent/cross-dept — тот же контракт, что и у остального /servers.
    await server_svc.get_server(db, identity, server_id)

    creds = await read_test_credentials(db, server_id)
    if creds is None:
        return {"exists": False}

    result: dict = {
        "exists": True,
        "username": creds["username"],
        "ssh_public_key": creds["ssh_public_key"],
        "rotated_at": creds["rotated_at"],
    }
    if reveal:
        result["password_b64"] = base64.b64encode(creds["password"].encode()).decode()
        result["ssh_private_key_b64"] = base64.b64encode(creds["ssh_private_key"].encode()).decode()
        audit_service.emit(
            "server.test_credentials_revealed",
            target_id=server_id, target_type="server",
            status="success", allowed=True,
        )
    else:
        audit_service.emit(
            "server.test_credentials_viewed",
            target_id=server_id, target_type="server",
            status="success", allowed=True,
        )
    return result


async def read_test_credentials(
    db: AsyncSession, server_id: str,
) -> dict | None:
    """Расшифрованная учётка исполнения теста сервера, либо None.

    Единственный canonical хранитель этого секрета — server_service (план
    миграции, §5.3): testing_service свою копию не держит и берёт значение
    отсюда.
    """
    stmt = select(ServerTestCredentials).where(
        ServerTestCredentials.server_id == server_id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    password = secrets_service.decrypt(
        row.password_encrypted,
        aad=secrets_service.aad_for_server_test_password(row.id),
    )
    private_key = secrets_service.decrypt(
        row.ssh_private_key_encrypted,
        aad=secrets_service.aad_for_server_test_ssh_key(row.id),
    )
    return {
        "username": row.username,
        "password": password,
        "ssh_public_key": row.ssh_public_key,
        "ssh_private_key": private_key,
        "rotated_at": row.rotated_at,
    }


# ── Старт пайплайна ──────────────────────────────────────────────────────────


async def start(
    db: AsyncSession,
    *,
    server_id: str,
    service_name: str,
    os_version_id: str,
    kernel: str,
    mode: str,
    test_username: str,
    requested_by_department_id: str | None,
    correlation_id: str,
) -> ServerPrepareForTestRequest:
    """Принять запрос на подготовку стенда и запустить цепочку restore→prepare.

    Идемпотентность — по `correlation_id`: повторный вызов возвращает ту же
    строку, ничего не диспатчит. Это важнее, чем кажется: у пайплайна нет
    быстрого «отменить», и второй restore на тот же диск посреди первого
    оставил бы стенд в неопределённом состоянии.

    Валидации, которые отбиваются до похода на стенд, дают терминальный
    `failed` сразу (с callback'ом), а не 4xx: для testing_service «ядра нет в
    каталоге» — такой же исход прогона, как «сервер не поднялся», и он должен
    приходить одним и тем же каналом.
    """
    existing = await get_by_correlation_id(db, correlation_id)
    if existing is not None:
        return existing

    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    if (
        requested_by_department_id is not None
        and requested_by_department_id != server.department_id
    ):
        # Маскируем под 404 — иначе разница 403/404 работает
        # enumeration-oracle'ом по чужим отделам (тот же приём, что у
        # worker-callback'ов и acquire-for-service).
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    if server.status == ServerStatus.DECOMMISSIONED:
        raise ConflictError(
            error_code="SERVER_DECOMMISSIONED",
            message="Server is decommissioned and cannot be prepared for tests",
        )
    if server.is_vms_hub:
        raise ConflictError(
            error_code="SERVER_IS_VMS_HUB",
            message="VMS-hub servers cannot be reimaged for test runs",
        )

    active = await get_active_request(db, server_id)
    if active is not None:
        raise ConflictError(
            error_code="PREPARE_FOR_TEST_ALREADY_RUNNING",
            message=(
                "Another prepare-for-test pipeline is already running for this "
                "server"
            ),
            details={"prepare_request_id": active.id},
        )

    request = ServerPrepareForTestRequest(
        id=prepare_for_test_request_id(),
        server_id=server_id,
        correlation_id=correlation_id,
        os_version_id=os_version_id,
        kernel=kernel,
        mode=mode,
        test_username=test_username,
        requested_by_department_id=requested_by_department_id,
        requested_by_service=service_name,
        status=PREPARE_FOR_TEST_IN_PROGRESS,
        stage=STEP_RESTORE,
    )
    db.add(request)
    await db.flush()

    audit_service.emit(
        AUDIT_ACTION_REQUESTED,
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "prepare_request_id": request.id,
            "correlation_id": correlation_id,
            "os_version_id": os_version_id,
            "kernel": kernel,
            "mode": mode,
            "test_username": test_username,
            "service_name": service_name,
            "department_id": server.department_id,
        },
    )

    os_version = await osv_repo.get_by_id(db, os_version_id)
    if os_version is None:
        await db.commit()
        await _finish_failed(
            db, request, STEP_RESTORE,
            f"OS version {os_version_id} is not in the catalog",
            server=server,
        )
        return request
    # Ядро сверяем с каталогом РЦ ДО любого SSH/ACS-вызова: восстанавливать
    # диск ради того, чтобы через час упасть на несуществующем ядре, — самая
    # дорогая из возможных ошибок в этом пайплайне.
    if kernel not in (os_version.kernels or []):
        await db.commit()
        await _finish_failed(
            db, request, STEP_KERNEL_CHANGE,
            f"kernel {kernel!r} is not listed for OS version {os_version.name}",
            server=server,
        )
        return request

    bootstrap_status = await bootstrap_password_svc.get_bootstrap_password_status(
        db, os_version.id,
    )
    if bootstrap_status is None or not bootstrap_status["has_password"]:
        await db.commit()
        await _finish_failed(
            db, request, STEP_RESTORE,
            (
                "no bootstrap password configured for this OS version; "
                "auto-prepare after reimage would have nothing to log in with"
            ),
            server=server,
        )
        return request

    acquired = await _hold_reservation(
        db, server, service_name=service_name, os_version_name=os_version.name,
        kernel=kernel,
    )
    request.reservation_acquired = acquired
    await db.commit()

    payload = {
        "server_id": server.id,
        "os_version_id": os_version.id,
        "version_name": os_version.name,
        "host": str(server.ip_address),
        "hostname": server.hostname,
        "ssh_port": server.ssh_port,
        "target_department_id": server.department_id,
    }
    try:
        task_id = await worker_client.dispatch_task(
            db=db,
            task_kind="acs.snapshot_restore",
            target_server_id=server.id,
            payload=payload,
            created_by=None,
            request_id=None,
            max_attempts=1,
        )
        request.restore_task_id = task_id
        await db.commit()
    except (ConflictError, ServiceUnavailableError) as exc:
        await db.rollback()
        request = await get_by_id(db, request.id)
        await _finish_failed(
            db, request, STEP_RESTORE,
            f"failed to dispatch ACS restore: {type(exc).__name__}",
        )
        return request
    return request


async def _hold_reservation(
    db: AsyncSession,
    server: Server,
    *,
    service_name: str,
    os_version_name: str,
    kernel: str,
) -> bool:
    """Занять стенд под подготовку. Возвращает True, если бронь взяли мы.

    Три случая:

    * бронь уже держит тот же сервис (он позвал `acquire-for-service` перед
      подготовкой) — обновляем только заметку, снимать её потом не наше дело;
    * сервер свободен — берём бронь себе, `pre_acs_busy_snapshot` пуст, на
      провале вернём в `free`;
    * держит кто-то другой (человек или другой сервис) — 409.
    """
    busy_note = f"ACS|{_BUSY_NOTE_ACTION}|{os_version_name}|{kernel}"
    held_by_caller = (
        server.busy_actor_type == BusyActorType.SERVICE
        and server.busy_service_name == service_name
        and server.busy_state != BusyState.FREE
    )
    if held_by_caller:
        await server_repo.update(db, server, {
            "busy_state": BusyState.ACS,
            "busy_note": busy_note,
        })
        return False

    if server.busy_state != BusyState.FREE:
        raise ConflictError(
            error_code="SERVER_ALREADY_BUSY",
            message="Server is reserved by someone else",
        )

    await server_repo.update(db, server, {
        "busy_state": BusyState.ACS,
        "busy_user_id": None,
        "busy_actor_type": BusyActorType.SERVICE,
        "busy_service_name": service_name,
        "busy_since": datetime.now(timezone.utc),
        "busy_note": busy_note,
        "pre_acs_busy_snapshot": reservation.capture_pre_acs_state(server),
    })
    return True


# ── Хуки существующей цепочки restore → prepare ──────────────────────────────


async def on_restore_failed(
    db: AsyncSession, server_id: str, error: str | None,
) -> None:
    """ACS не восстановила диск — пайплайн дальше не идёт.

    Зовётся из `internal_service.record_acs_snapshot_restore_done` в ветке
    `succeeded=False`; бронь там уже вернули к до-ACS состоянию, поэтому свою
    мы не трогаем.
    """
    request = await get_active_request(db, server_id)
    if request is None:
        return
    await _finish_failed(
        db, request, STEP_RESTORE, error or "ACS restore failed", release=False,
    )


async def on_restore_stalled(
    db: AsyncSession, server_id: str, reason: str,
) -> None:
    """Restore прошёл, но авто-prepare не стартовал (нет bootstrap-пароля и т.п.).

    Тот же смысл, что `on_restore_failed`, но диск уже переписан, и бронь
    остаётся на сервере — снимать её нам нельзя, оператор разбирается руками.
    """
    request = await get_active_request(db, server_id)
    if request is None:
        return
    await _finish_failed(db, request, STEP_PREPARE, reason, release=False)


async def on_server_prepared(
    db: AsyncSession, server: Server,
) -> ServerPrepareForTestRequest | None:
    """Сервер стал managed — запускаем новые шаги пайплайна.

    Возвращает активный запрос, если он есть (тогда caller НЕ снимает
    ACS-бронь: она нужна до конца подготовки), иначе None.

    Здесь же выписывается учётка исполнения теста: plaintext уезжает в
    Redis-stash под одноразовым ключом, в payload воркера едет только ссылка.
    """
    request = await get_active_request(db, server.id)
    if request is None:
        return None

    creds = await _issue_test_credentials(db, server, request.test_username)
    request.stage = STEP_USER_PROVISION
    await db.commit()

    stash_key = worker_client.dispatch_creds_key(dispatch_creds_id())
    try:
        await worker_client.store_dispatch_creds(stash_key, {
            "test_username": creds["username"],
            "test_password": creds["password"],
            "test_ssh_public_key": creds["ssh_public_key"],
        })
    except Exception as exc:  # noqa: BLE001 — любой runtime-фейл Redis
        await worker_client.delete_dispatch_creds(stash_key)
        await _finish_failed(
            db, request, STEP_USER_PROVISION,
            f"failed to stash test credentials: {type(exc).__name__}",
            release=False,
        )
        return request

    payload = {
        "server_id": server.id,
        "prepare_request_id": request.id,
        "target_department_id": server.department_id,
        "host": str(server.ip_address),
        "ssh_port": server.ssh_port,
        "is_managed": True,
        "management_user": server.management_user,
        "kernel": request.kernel,
        "mode": request.mode,
        "test_creds_key": stash_key,
    }
    try:
        task_id = await worker_client.dispatch_task(
            db=db,
            task_kind="server.prepare_for_test",
            target_server_id=server.id,
            payload=payload,
            created_by=None,
            request_id=None,
            max_attempts=1,
        )
        request.provision_task_id = task_id
        await db.commit()
    except (ConflictError, ServiceUnavailableError) as exc:
        await db.rollback()
        await worker_client.delete_dispatch_creds(stash_key)
        request = await get_by_id(db, request.id)
        await _finish_failed(
            db, request, STEP_USER_PROVISION,
            f"failed to dispatch prepare_for_test task: {type(exc).__name__}",
            release=False,
        )
    return request


# ── Завершение ───────────────────────────────────────────────────────────────


async def complete_from_worker(
    db: AsyncSession,
    request: ServerPrepareForTestRequest,
    *,
    succeeded: bool,
    failed_step: str | None,
    error: str | None,
) -> tuple[str, bool]:
    """Записать исход новых шагов пайплайна и отправить callback наружу.

    Возвращает `(status, callback_delivered)`. Идемпотентно: повторный
    callback воркера по уже завершённому запросу заново шлёт исходящий
    callback (потребитель мог его не получить), но не переписывает исход.

    `error` на успехе — не провал, а non-fatal предупреждение (например,
    режим безопасности перед сменой не совпал с ожидаемым): воркер кладёт
    его в то же поле, что и причину провала, `_deliver_callback` прокидывает
    его testing_service отдельным необязательным `warning`.
    """
    if request.status != PREPARE_FOR_TEST_IN_PROGRESS:
        delivered = await _deliver_callback(db, request)
        return request.status, delivered

    if succeeded:
        request.status = PREPARE_FOR_TEST_SUCCEEDED
        request.stage = STEP_REBOOT_VERIFY
        request.failed_step = None
        request.error = (error or "")[:_ERROR_MAX_LEN] or None
    else:
        request.status = PREPARE_FOR_TEST_FAILED
        request.failed_step = failed_step or STEP_USER_PROVISION
        request.stage = request.failed_step
        request.error = (error or "")[:_ERROR_MAX_LEN] or None
    request.completed_at = datetime.now(timezone.utc)
    await db.commit()

    audit_service.emit(
        AUDIT_ACTION_COMPLETED,
        target_id=request.server_id, target_type="server",
        status="success" if succeeded else "failure", allowed=True,
        details={
            "prepare_request_id": request.id,
            "correlation_id": request.correlation_id,
            "succeeded": succeeded,
            "failed_step": request.failed_step,
            "kernel": request.kernel,
            "os_version_id": request.os_version_id,
        },
    )

    if not succeeded and request.reservation_acquired:
        await _release_own_reservation(db, request)

    delivered = await _deliver_callback(db, request)
    return request.status, delivered


async def _finish_failed(
    db: AsyncSession,
    request: ServerPrepareForTestRequest | None,
    step: str,
    error: str,
    *,
    server: Server | None = None,
    release: bool = True,
) -> None:
    """Терминально провалить запрос: записать шаг, снять свою бронь, отдать callback."""
    if request is None or request.status != PREPARE_FOR_TEST_IN_PROGRESS:
        return
    request.status = PREPARE_FOR_TEST_FAILED
    request.failed_step = step
    request.stage = step
    request.error = error[:_ERROR_MAX_LEN]
    request.completed_at = datetime.now(timezone.utc)
    await db.commit()

    audit_service.emit(
        AUDIT_ACTION_COMPLETED,
        target_id=request.server_id, target_type="server",
        status="failure", allowed=True,
        details={
            "prepare_request_id": request.id,
            "correlation_id": request.correlation_id,
            "succeeded": False,
            "failed_step": step,
            "kernel": request.kernel,
            "os_version_id": request.os_version_id,
        },
    )

    if release and request.reservation_acquired:
        await _release_own_reservation(db, request, server=server)
    await _deliver_callback(db, request)


async def _release_own_reservation(
    db: AsyncSession,
    request: ServerPrepareForTestRequest,
    *,
    server: Server | None = None,
) -> None:
    """Вернуть бронь, взятую этим запросом. Чужую не трогаем.

    Проверяем, что держатель — всё ещё мы: между стартом и провалом кто-то мог
    снять бронь и взять её заново (например, оператор через админку), и
    возвращать её в `free` из-под него нельзя.
    """
    if server is None:
        server = await server_repo.get_by_id(db, request.server_id)
    if server is None:
        return
    if (
        server.busy_actor_type != BusyActorType.SERVICE
        or server.busy_service_name != request.requested_by_service
    ):
        return
    reservation.restore_pre_acs_state(server)
    await db.flush()
    await db.commit()


async def _deliver_callback(
    db: AsyncSession, request: ServerPrepareForTestRequest,
) -> bool:
    """Собрать тело callback'а и отправить его в testing_service.

    На успехе тело несёт логин, пароль и приватный ключ учётки исполнения
    теста — testing_service держит их ровно столько, сколько идёт прогон, и
    своей копии не хранит (canonical хранитель — мы).
    """
    body: dict = {
        "correlation_id": request.correlation_id,
        "succeeded": request.status == PREPARE_FOR_TEST_SUCCEEDED,
    }
    if request.status == PREPARE_FOR_TEST_SUCCEEDED:
        creds = await read_test_credentials(db, request.server_id)
        if creds is None:
            # Строка кред исчезла между провижном и callback'ом (сервер снесён
            # каскадом). Отдавать «успех без кред» нельзя — прогон всё равно
            # не запустится, честнее провалить.
            request.status = PREPARE_FOR_TEST_FAILED
            request.failed_step = STEP_USER_PROVISION
            request.error = "test credentials row disappeared before callback"
            await db.commit()
            body["succeeded"] = False
            body["failed_step"] = STEP_USER_PROVISION
            body["error"] = request.error
        else:
            body["test_username"] = creds["username"]
            body["test_password"] = creds["password"]
            body["test_ssh_private_key"] = creds["ssh_private_key"]
            if request.error:
                # Не провал — например, режим безопасности перед сменой не
                # совпал с ожидаемым, но пайплайн всё равно довёл дело до
                # конца. testing_service решает сам, что с этим делать.
                body["warning"] = request.error
    else:
        body["failed_step"] = request.failed_step
        body["error"] = request.error

    delivered, attempts, last_error = (
        await testing_client.send_prepare_for_test_completed(request.id, body)
    )
    request.callback_attempts = (request.callback_attempts or 0) + attempts
    request.callback_last_error = last_error
    if delivered:
        request.callback_delivered_at = datetime.now(timezone.utc)
    await db.commit()
    return delivered


__all__ = [
    "AUDIT_ACTION_COMPLETED",
    "AUDIT_ACTION_REQUESTED",
    "complete_from_worker",
    "get_active_request",
    "get_by_correlation_id",
    "get_by_id",
    "on_restore_failed",
    "on_restore_stalled",
    "on_server_prepared",
    "read_test_credentials",
    "reveal_test_credentials",
    "start",
]
