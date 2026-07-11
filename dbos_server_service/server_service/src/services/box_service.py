"""Use cases для боксов-заготовок (boxes) — CRUD + reveal пароля образа.

Бокс — пер-департамент каталожная запись образа для создания ВМ. Доступ
резолвится тип-wide матрицей `(box, *)` плюс изоляцией отдела: чужой бокс
скрыт за 404 (`BOX_NOT_FOUND`), чтобы не выдавать факт существования.

Карточка доступна по `view`. Если вызывающий вдобавок держит `view_password`,
тот же GET доносит пароль предустановленного пользователя в base64 — отдельной
reveal-ручки нет. Пароль хранится envelope-форматом secrets_service (AES-256-GCM
с версией ключа в wire-префиксе), AAD привязывает ciphertext к строке бокса.

Скачивание/импорт боксов по URL — отдельный поток; здесь только каталог.
"""

import base64
import logging

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, VmTaskKind
from src.core.exceptions import (
    AppException,
    AuthorizationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from src.dependencies.idempotency import read_idempotency_key
from src.models import Box
from src.repositories import box as repo
from src.repositories import server as server_repo
from src.schemas.box import BoxCreate, BoxUpdate
from src.schemas.box import BoxDownloadStateCallbackRequest
from src.schemas.identity import IdentityContext
from src.services import (
    audit_context,
    audit_service,
    permissions,
    secrets_service,
    worker_client,
)
from src.services.audit_helpers import emit_denied_on_authz_error
from src.utils.ids import box_id as new_id

logger = logging.getLogger(__name__)


def aad_for_box_base_user_password(box_id: str) -> bytes:
    """AAD для `boxes.base_user_password_encrypted` строки `box_id`.

    Формат — `"box_base_user_password|boxes|<id>"`. Привязывает ciphertext к
    конкретной строке бокса: swap в другую строку → InvalidTag.
    """
    return f"box_base_user_password|boxes|{box_id}".encode()


async def list_boxes(
    db: AsyncSession, identity: IdentityContext, *, limit: int, offset: int,
) -> tuple[list[Box], int]:
    """Список боксов своего отдела. Право `(box, view)`."""
    with emit_denied_on_authz_error(
        "box.list", target_type="box", identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.BOX, Action.VIEW)
    if identity.department_id is None:
        return [], 0
    items = await repo.list_in_department(
        db, identity.department_id, limit=limit, offset=offset
    )
    total = await repo.count_in_department(db, identity.department_id)
    return items, total


async def get_box(
    db: AsyncSession, identity: IdentityContext, box_id: str,
) -> tuple[Box, str | None]:
    """Карточка бокса + (опционально) раскрытый пароль образного пользователя.

    Доступна по `view` или `view_password`: держателю `view_password` голый
    `view` не нужен. Второй элемент кортежа — base64(plaintext пароля) при
    наличии `view_password` и сохранённого пароля, иначе `None`. Чужой отдел /
    несуществующий бокс → 404 BOX_NOT_FOUND.
    """
    has_password_action = await permissions.has_action(
        db, identity, EntityType.BOX, Action.VIEW_PASSWORD
    )
    with emit_denied_on_authz_error(
        "box.view",
        target_id=box_id,
        target_type="box",
        identity=identity,
    ):
        if not has_password_action:
            await permissions.require_action(db, identity, EntityType.BOX, Action.VIEW)

    box = await repo.get_by_id(db, box_id)
    if box is None or box.department_id != identity.department_id:
        audit_service.emit(
            "box.view",
            target_id=box_id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="BOX_NOT_FOUND", message="Box not found")

    revealed: str | None = None
    if has_password_action:
        revealed = await _reveal_base_user_password(box)

    audit_service.emit(
        "box.view",
        target_id=box.id, target_type="box",
        status="success", allowed=True,
        details={
            "department_id": box.department_id,
            "with_password": has_password_action,
        },
    )
    return box, revealed


async def create_box(
    db: AsyncSession, identity: IdentityContext, payload: BoxCreate,
) -> Box:
    """INSERT бокса. Право `(box, create)`, изоляция отдела."""
    with emit_denied_on_authz_error(
        "box.create",
        target_type="box",
        extra_details={"department_id": payload.department_id},
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.BOX, Action.CREATE)

    if payload.department_id != identity.department_id:
        audit_service.emit(
            "box.create", target_type="box", status="failure", allowed=True,
            details={"reason": "department_isolation", "department_id": payload.department_id},
        )
        raise ConflictError(
            error_code="DEPARTMENT_ISOLATION",
            message="Cannot create a box in a different department",
        )

    box_id = new_id()
    plaintext = payload.base_user_password()
    encrypted = (
        secrets_service.encrypt(plaintext, aad=aad_for_box_base_user_password(box_id))
        if plaintext is not None
        else None
    )
    data = {
        "id": box_id,
        "department_id": payload.department_id,
        "name": payload.name,
        "format": payload.format,
        "download_url": payload.download_url,
        "base_user_login": payload.base_user_login,
        "base_user_password_encrypted": encrypted,
        "os_versions": list(payload.os_versions),
        "initial_snapshots": list(payload.initial_snapshots),
        "created_by": identity.user_id,
    }
    try:
        box = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на создании бокса: %s", type(exc.orig).__name__)
        audit_service.emit(
            "box.create", target_type="box", status="failure", allowed=True,
            details={"reason": "duplicate", "name": payload.name},
        )
        raise ConflictError(
            error_code="BOX_DUPLICATE",
            message="A box with this name already exists in the department",
        ) from exc
    await db.refresh(box)
    audit_service.emit(
        "box.create", target_id=box.id, target_type="box",
        status="success", allowed=True,
        details={
            "name": box.name,
            "format": box.format,
            "department_id": box.department_id,
        },
    )
    return box


async def update_box(
    db: AsyncSession, identity: IdentityContext, box_id: str, payload: BoxUpdate,
) -> Box:
    """PATCH бокса (частично). Право `(box, update)` + изоляция отдела."""
    with emit_denied_on_authz_error(
        "box.update",
        target_id=box_id,
        target_type="box",
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.BOX, Action.UPDATE)

    box = await repo.get_by_id(db, box_id)
    if box is None or box.department_id != identity.department_id:
        audit_service.emit(
            "box.update", target_id=box_id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="BOX_NOT_FOUND", message="Box not found")

    fields = payload.model_dump(exclude_unset=True)
    # Пароль переводим из base64 в шифротекст отдельно — в БД едет
    # base_user_password_encrypted, а не base_user_password_b64.
    changes: dict = {}
    password_changed = False
    for key, value in fields.items():
        if key == "base_user_password_b64":
            plaintext = payload.base_user_password()
            changes["base_user_password_encrypted"] = (
                secrets_service.encrypt(
                    plaintext, aad=aad_for_box_base_user_password(box.id)
                )
                if plaintext is not None
                else None
            )
            password_changed = True
        else:
            changes[key] = value
    if not changes:
        return box

    try:
        await repo.update(db, box, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning("IntegrityError на обновлении бокса %s: %s", box_id, type(exc.orig).__name__)
        audit_service.emit(
            "box.update", target_id=box.id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": sorted(fields.keys())},
        )
        raise ConflictError(
            error_code="BOX_DUPLICATE",
            message="A box with this name already exists in the department",
        ) from exc
    await db.refresh(box)
    changed = sorted(k for k in fields.keys() if k != "base_user_password_b64")
    if password_changed:
        changed.append("base_user_password")
    audit_service.emit(
        "box.update", target_id=box.id, target_type="box",
        status="success", allowed=True,
        details={"changed": changed, "department_id": box.department_id},
    )
    return box


async def delete_box(
    db: AsyncSession, identity: IdentityContext, box_id: str,
) -> None:
    """DELETE бокса. Право `(box, delete)` + изоляция отдела."""
    with emit_denied_on_authz_error(
        "box.delete",
        target_id=box_id,
        target_type="box",
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.BOX, Action.DELETE)

    box = await repo.get_by_id(db, box_id)
    if box is None or box.department_id != identity.department_id:
        audit_service.emit(
            "box.delete", target_id=box_id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="BOX_NOT_FOUND", message="Box not found")

    dept = box.department_id
    name = box.name
    await repo.delete(db, box)
    await db.commit()
    audit_service.emit(
        "box.delete", target_id=box_id, target_type="box",
        status="success", allowed=True,
        details={"name": name, "department_id": dept},
    )


async def resolve_box_for_dispatch(
    db: AsyncSession, identity: IdentityContext, box_id: str,
) -> dict:
    """Собрать фрагмент dispatch-payload `vm.create` из бокса реестра.

    Фетчит бокс, проверяет изоляцию отдела (чужой/несуществующий → 404
    BOX_NOT_FOUND) и отдаёт то, что воркер читает при сборке ВМ: base_user-креды
    образа (`base_user_login` + расшифрованный `base_user_password`), список
    версий ОС (`os_versions`) и адрес скачивания (`download_url`). Пароль
    расшифровывается под AAD бокса и наружу (в HTTP-ответ) не отдаётся — уезжает
    только воркеру. Ключи, для которых у бокса нет данных, в фрагмент не кладём.
    """
    box = await repo.get_by_id(db, box_id)
    if box is None or box.department_id != identity.department_id:
        raise NotFoundError(error_code="BOX_NOT_FOUND", message="Box not found")

    fragment: dict = {}
    if box.base_user_login is not None:
        fragment["base_user_login"] = box.base_user_login
    if box.base_user_password_encrypted is not None:
        fragment["base_user_password"] = secrets_service.decrypt(
            box.base_user_password_encrypted,
            aad=aad_for_box_base_user_password(box.id),
        )
    if box.os_versions:
        fragment["os_versions"] = list(box.os_versions)
    if box.download_url:
        fragment["download_url"] = box.download_url
    return fragment


def _hub_download_payload(box: Box, hub) -> dict:
    """Собрать payload задачи `box.download` из бокса и hub-сервера.

    Hub-блок адресации — как у VM-задач (SSH всегда по IP). `box_name` —
    имя, под которым `vm.create` ищет образ в пуле; `download_url`/`format` —
    из каталожной записи бокса.
    """
    return {
        "box_id": box.id,
        "box_name": box.name,
        "download_url": box.download_url,
        "format": box.format,
        "target_department_id": hub.department_id,
        # SSH-таргет — ВСЕГДА IP hub'а, не hostname.
        "server_id": hub.id,
        "hub_server_id": hub.id,
        "host": str(hub.ip_address),
        "ssh_port": hub.ssh_port,
        "is_managed": hub.is_managed,
        "management_user": hub.management_user,
    }


async def dispatch_download(
    db: AsyncSession,
    identity: IdentityContext,
    request: Request,
    box_id: str,
    hub_server_id: str,
) -> tuple[Box, str]:
    """Запустить скачивание бокса на hub. Право `(box, update)` + изоляция отдела.

    Проверяет бокс своего отдела (чужой/несуществующий → 404 BOX_NOT_FOUND),
    наличие `download_url` (иначе 400 BOX_NO_DOWNLOAD_URL) и hub-сервер: свой
    отдел (иначе 404 HUB_NOT_FOUND) и подготовленность как VMS-hub (иначе 409
    HUB_NOT_PREPARED). Ставит боксу `download_status='downloading'`, диспатчит
    `box.download` и коммитит (outbox-row в одну транзакцию с апдейтом бокса).
    Возвращает `(box, task_id)`.
    """
    with emit_denied_on_authz_error(
        "box.download",
        target_id=box_id,
        target_type="box",
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.BOX, Action.UPDATE)

    box = await repo.get_by_id(db, box_id)
    if box is None or box.department_id != identity.department_id:
        audit_service.emit(
            "box.download", target_id=box_id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "not_found_or_cross_dept"},
        )
        raise NotFoundError(error_code="BOX_NOT_FOUND", message="Box not found")

    if not box.download_url:
        audit_service.emit(
            "box.download", target_id=box.id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "no_download_url", "department_id": box.department_id},
        )
        raise BadRequestError(
            error_code="BOX_NO_DOWNLOAD_URL",
            message="Box has no download_url to fetch from",
        )

    hub = await server_repo.get_by_id(db, hub_server_id)
    if hub is None or hub.department_id != identity.department_id:
        audit_service.emit(
            "box.download", target_id=box.id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "hub_not_found_or_cross_dept", "hub_server_id": hub_server_id},
        )
        raise NotFoundError(error_code="HUB_NOT_FOUND", message="Hub server not found")
    if not hub.is_vms_hub:
        audit_service.emit(
            "box.download", target_id=box.id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "hub_not_prepared", "hub_server_id": hub.id},
        )
        raise ConflictError(
            error_code="HUB_NOT_PREPARED",
            message="Server is not prepared as a VMS-hub; run prepare-vms-hub first",
        )

    await repo.update(
        db, box, {"download_status": "downloading", "download_last_error": None},
    )
    payload = _hub_download_payload(box, hub)
    try:
        task_id = await worker_client.dispatch_task(
            db=db,
            task_kind=VmTaskKind.BOX_DOWNLOAD.value,
            target_server_id=hub.id,
            target_resource_id=box.id,
            payload=payload,
            created_by=identity.user_id,
            request_id=getattr(request.state, "request_id", None),
            idempotency_key=read_idempotency_key(request),
        )
    except (ConflictError, AppException) as exc:
        await db.rollback()
        audit_service.emit(
            "box.download", target_id=box.id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "dispatch_failed", "hub_server_id": hub.id,
                     "error_code": getattr(exc, "error_code", type(exc).__name__)},
        )
        raise
    await db.commit()
    await db.refresh(box)
    audit_service.emit(
        "box.download", target_id=box.id, target_type="box",
        status="success", allowed=True,
        details={
            "task_id": task_id, "hub_server_id": hub.id,
            "department_id": box.department_id, "format": box.format,
        },
    )
    return box, task_id


async def record_download_status(
    db: AsyncSession,
    identity: IdentityContext,
    box_id: str,
    payload: BoxDownloadStateCallbackRequest,
    *,
    target_department_id: str | None = None,
) -> dict:
    """Internal callback воркера: исход `box.download` (ready/error).

    Гейт — `(server, prepare_callback)` (тот же worker_bot-грант, что у прочих
    callback'ов). Cross-dept скоуп — заголовок `X-Target-Department-Id`, который
    обязан совпасть с отделом бокса (иначе 404-маска, как в internal-эндпоинтах).
    Пишет `download_status` и `download_last_error` на строке бокса.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "box.download_state", target_id=box_id, target_type="box",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    box = await repo.get_by_id(db, box_id)
    if box is None:
        audit_service.emit(
            "box.download_state", target_id=box_id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "box_not_found"},
        )
        raise NotFoundError(error_code="BOX_NOT_FOUND", message="Box not found")

    # Cross-dept гард: заголовок обязателен и должен совпасть с отделом бокса.
    if target_department_id is None:
        audit_service.emit(
            "box.download_state", target_id=box_id, target_type="box",
            status="denied", allowed=False,
            details={"reason": "missing_target_department_header",
                     "box_department_id": box.department_id},
        )
        raise AuthorizationError(
            error_code="TARGET_DEPARTMENT_HEADER_REQUIRED",
            message="X-Target-Department-Id header is required for internal endpoints",
            details={"target_id": box_id},
        )
    if target_department_id != box.department_id:
        # Чужой отдел — маскируем под not-found (как в internal_service).
        audit_service.emit(
            "box.download_state", target_id=box_id, target_type="box",
            status="denied", allowed=False,
            details={"reason": "target_department_mismatch",
                     "box_department_id": box.department_id,
                     "header_department_id": target_department_id},
        )
        raise NotFoundError(error_code="BOX_NOT_FOUND", message="Box not found")

    changes: dict = {"download_status": payload.status}
    changes["download_last_error"] = payload.error if payload.status == "error" else None
    await repo.update(db, box, changes)
    await db.commit()
    audit_service.emit(
        "box.download_state", target_id=box.id, target_type="box",
        status="success", allowed=True,
        details={"download_status": payload.status, "department_id": box.department_id,
                 "caller_type": identity.subject_type},
    )
    return {"ok": True, "box_id": box.id, "download_status": payload.status}


async def _reveal_base_user_password(box: Box) -> str | None:
    """Расшифровать пароль образного пользователя в base64 + аудит раскрытия.

    Вызывается из `get_box` только после проверки `view_password`. Возвращает
    `None`, если пароль у бокса не сохранён. Сломанный ciphertext поднимает
    `DECRYPT_FAILED` + failure-аудит.
    """
    audit_action = "box.base_user_password_revealed"
    if box.base_user_password_encrypted is None:
        return None

    aad = aad_for_box_base_user_password(box.id)
    try:
        plain = secrets_service.decrypt(box.base_user_password_encrypted, aad=aad)
    except AppException:
        audit_service.emit(
            audit_action, target_id=box.id, target_type="box",
            status="failure", allowed=True,
            details={"reason": "decrypt_failed", "department_id": box.department_id},
        )
        raise

    actor_id = audit_context.get_context().actor_id
    audit_service.emit(
        audit_action, target_id=box.id, target_type="box",
        status="success", allowed=True,
        details={
            "department_id": box.department_id,
            "base_user_login": box.base_user_login,
            "actor_id": actor_id,
        },
    )
    return base64.b64encode(plain.encode()).decode("ascii")
