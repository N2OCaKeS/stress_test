"""Гейт деструктивных операций по брони сервера (busy_state).

Бронь (`servers.busy_state='busy'`) сама по себе не запрещает деструктивные
операции — её снимает только этот гейт. Логика: когда сервер занят, менять его
состояние (power, provision/deprovision, rotate, delete и т.п.) вправе только
владелец брони (`busy_user_id`) либо администратор отдела сервера. Остальным
прилетает 409 `SERVER_RESERVED`.

Read-операции (view/list/get, чтение пакетов, console-read, inventory) и сам
release брони гейт не трогает — владелец/админ должны мочь освободить занятый
сервер.

«Админ» определяется так же, как для recreate_login
(`server_account._is_account_recreate_admin`): platform `department_admin`
своего отдела ЛИБО носитель service-роли `admin` в server_service. Platform
`account_admin` сюда не доходит — его режет `platform_admin_guard` middleware.
"""

from src.core.constants import BusyState, PlatformRole, SERVICE_NAME, ServiceRole
from src.core.exceptions import ConflictError
from src.models import Server
from src.schemas.identity import IdentityContext
from src.services import audit_service


def is_server_admin(identity: IdentityContext, server: Server) -> bool:
    """True, если caller — администратор по отношению к этому серверу.

    Админ = platform `department_admin` отдела сервера ЛИБО носитель
    service-роли `admin` в server_service. Тот же контракт, что у
    `server_account._is_account_recreate_admin`, но для сущности server.
    """
    if (
        identity.platform_role == PlatformRole.DEPARTMENT_ADMIN
        and identity.department_id is not None
        and identity.department_id == server.department_id
    ):
        return True
    return ServiceRole.ADMIN in identity.roles_for_service(SERVICE_NAME)


def is_reserved_for_other(identity: IdentityContext, server: Server) -> bool:
    """True, если сервер забронирован НЕ под caller'а и caller не админ.

    Чистый предикат без сайд-эффектов — удобен там, где нужно решить, гейтить
    операцию или нет, без эмита аудита (например, при выборе ветки fan-out'а).
    """
    if server.busy_state != BusyState.BUSY:
        return False
    if server.busy_user_id == identity.user_id:
        return False
    return not is_server_admin(identity, server)


def ensure_not_reserved_for(
    identity: IdentityContext,
    server: Server,
    *,
    action: str,
) -> None:
    """Пропустить деструктивную операцию `action` только если сервер не занят
    под чужого владельца.

    Если `busy_state='busy'` и caller не владелец брони и не админ — пишет
    WARNING-аудит `server.reservation_denied` и бросает 409 `SERVER_RESERVED`
    с указанием, кто держит бронь (`busy_user_id`, `busy_note`). В остальных
    случаях возвращается молча.

    `action` — машинный ключ операции (например `server.power_on`,
    `server_account.delete`), попадает в детали аудита для трассировки.
    """
    if not is_reserved_for_other(identity, server):
        return
    details = {
        "blocked_action": action,
        "server_id": server.id,
        "department_id": server.department_id,
        "busy_user_id": server.busy_user_id,
        "busy_note": server.busy_note,
    }
    if identity.subject_type is not None:
        details["subject_type"] = identity.subject_type
    audit_service.emit(
        "server.reservation_denied",
        target_id=server.id,
        target_type="server",
        status="denied",
        allowed=False,
        details=details,
    )
    raise ConflictError(
        error_code="SERVER_RESERVED",
        message=(
            "Server is reserved by another user; destructive operations are "
            "limited to the reservation owner or a department/service admin"
        ),
        details={
            "busy_user_id": server.busy_user_id,
            "busy_note": server.busy_note,
        },
    )
