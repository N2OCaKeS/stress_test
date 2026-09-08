"""Гейт деструктивных операций по брони сервера (busy_state).

Бронь (`servers.busy_state` в `busy`/`testing`) сама по себе не запрещает
деструктивные операции — её снимает только этот гейт. Логика: когда сервер
занят, менять его состояние (power, provision/deprovision, rotate, delete и
т.п.) вправе только владелец брони (`busy_user_id`) либо администратор отдела
сервера. Остальным прилетает 409 `SERVER_RESERVED`.

`testing` — бронь исполнения теста, её держит сервис
(`busy_actor_type='service'`), а не человек, поэтому владельца-пользователя у
неё нет и проверка `busy_user_id` никого не пропускает: остаются админ (тот же
override, что и для человеческой брони) и сам держащий сервис — он ходит
своим internal-каналом (`release-for-service`/`service-status`), не через этот
гейт. `acs` в гейт не входит: у него отдельный, более строгий
`ensure_not_acs_locked` — там даже владелец прежней брони не проходит.

Read-операции (view/list/get, чтение пакетов, console-read, inventory) и сам
release брони гейт не трогает — владелец/админ должны мочь освободить занятый
сервер.

«Админ» определяется так же, как для recreate_login
(`server_account._is_account_recreate_admin`): platform `department_admin`
своего отдела ЛИБО носитель service-роли `admin` в server_service. Platform
`account_admin` сюда не доходит — его режет `platform_admin_guard` middleware.
"""

import logging
from datetime import datetime

from src.core.constants import (
    BusyActorType,
    BusyState,
    PlatformRole,
    SERVICE_NAME,
    ServiceRole,
)
from src.core.exceptions import ConflictError
from src.models import Server
from src.schemas.identity import IdentityContext
from src.services import audit_service

logger = logging.getLogger(__name__)


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


# Состояния, которые гейт трактует как «сервер занят под кого-то». `acs` сюда
# не входит — у него свой, более строгий `ensure_not_acs_locked`; `updating` —
# тоже свой, `ensure_not_updating`, где не проходит вообще никто.
_RESERVED_STATES: frozenset[str] = frozenset({BusyState.BUSY, BusyState.TESTING})


def is_reserved_for_other(identity: IdentityContext, server: Server) -> bool:
    """True, если сервер забронирован НЕ под caller'а и caller не админ.

    Чистый предикат без сайд-эффектов — удобен там, где нужно решить, гейтить
    операцию или нет, без эмита аудита (например, при выборе ветки fan-out'а).

    Сервисная бронь (`busy_actor_type='service'`) сюда попадает наравне с
    человеческой: `busy_user_id` у неё пуст (CHECK `ck_servers_busy_actor`),
    поэтому владельцем не окажется никто и пройдёт только админ.
    """
    if server.busy_state not in _RESERVED_STATES:
        return False
    if server.busy_user_id == identity.user_id:
        return False
    return not is_server_admin(identity, server)


def ensure_not_updating(
    identity: IdentityContext,
    server: Server,
    *,
    action: str,
) -> None:
    """Отбить любую операцию над сервером, пока идёт обновление ОС.

    Пока `busy_state='updating'` (системная блокировка на время astra_update),
    менять состояние сервера нельзя НИКОМУ — ни владельцу брони, ни админу:
    посреди `astra-update` любой параллельный prepare/power/inventory/account-op
    может сломать бокс. Отличие от `ensure_not_reserved_for`, где владелец и
    админ проходят.

    Пишет WARNING-аудит `server.astra_update_locked` и бросает 409
    `SERVER_UPDATING`. Снимает блокировку только callback воркера (успех/ошибка).
    """
    if server.busy_state != BusyState.UPDATING:
        return
    details = {
        "blocked_action": action,
        "server_id": server.id,
        "department_id": server.department_id,
        "busy_note": server.busy_note,
    }
    if identity.subject_type is not None:
        details["subject_type"] = identity.subject_type
    audit_service.emit(
        "server.astra_update_locked",
        target_id=server.id,
        target_type="server",
        status="denied",
        allowed=False,
        details=details,
    )
    raise ConflictError(
        error_code="SERVER_UPDATING",
        message=(
            "Server is being updated (astra_update in progress); all other "
            "operations are blocked until the update completes"
        ),
        details={"busy_note": server.busy_note},
    )


def ensure_not_reserved_for(
    identity: IdentityContext,
    server: Server,
    *,
    action: str,
) -> None:
    """Пропустить деструктивную операцию `action` только если сервер не занят
    под чужого владельца или системным обновлением ОС.

    Сначала — жёсткий гейт обновления ОС (`ensure_not_updating`): пока сервер
    `updating`, операция отбивается 409 `SERVER_UPDATING` для всех без
    исключения. Затем — обычная бронь: если `busy_state` в `busy`/`testing` и
    caller не владелец брони и не админ — пишет WARNING-аудит
    `server.reservation_denied` и бросает 409 `SERVER_RESERVED` с указанием,
    кто держит бронь (`busy_user_id`/`busy_service_name`, `busy_note`).
    В остальных случаях возвращается молча.

    `action` — машинный ключ операции (например `server.power_on`,
    `server_account.delete`), попадает в детали аудита для трассировки.
    """
    ensure_not_updating(identity, server, action=action)
    if not is_reserved_for_other(identity, server):
        return
    details = {
        "blocked_action": action,
        "server_id": server.id,
        "department_id": server.department_id,
        "busy_state": server.busy_state,
        "busy_user_id": server.busy_user_id,
        "busy_actor_type": server.busy_actor_type,
        "busy_service_name": server.busy_service_name,
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
            "Server is reserved by another actor; destructive operations are "
            "limited to the reservation owner or a department/service admin"
        ),
        details={
            "busy_user_id": server.busy_user_id,
            "busy_service_name": server.busy_service_name,
            "busy_note": server.busy_note,
        },
    )


def capture_pre_acs_state(server: Server) -> dict:
    """Снять снимок текущей брони перед переходом сервера в `busy_state=acs`.

    Возвращает dict для записи в `server.pre_acs_busy_snapshot` — сохраняет
    `busy_state`/`busy_user_id`/`busy_actor_type`/`busy_service_name`/
    `busy_note`/`busy_since` в том виде, в каком они были непосредственно
    перед ACS-dispatch'ем, чтобы по завершении операции вернуть сервер туда
    же, а не безусловно в `free` (см. `restore_pre_acs_state`). `busy_since`
    кладём ISO-строкой — JSONB не хранит datetime нативно.

    Актор снимается вместе с остальным: сам ACS-dispatch перевешивает бронь
    на сервисного актора (`service`/`acs`), и без этих двух ключей restore
    вернул бы `busy_user_id` прежнего владельца, оставив `busy_service_name`
    от ACS — комбинация, которую отбивает `ck_servers_busy_actor`.
    """
    return {
        "busy_state": server.busy_state,
        "busy_user_id": server.busy_user_id,
        "busy_actor_type": server.busy_actor_type,
        "busy_service_name": server.busy_service_name,
        "busy_note": server.busy_note,
        "busy_since": server.busy_since.isoformat() if server.busy_since else None,
    }


def restore_pre_acs_state(server: Server) -> None:
    """Вернуть бронь сервера к состоянию до ACS-операции.

    Читает `server.pre_acs_busy_snapshot` и раскладывает его обратно в
    `busy_state`/`busy_user_id`/`busy_actor_type`/`busy_service_name`/
    `busy_note`/`busy_since`, затем очищает сам
    снимок. Если снимка нет (старые данные до миграции) или он повреждён —
    молча падаем на прежнее дефолтное поведение (`busy_state=free`), а не
    бросаем исключение: снятие ACS-блокировки не должно зависать из-за
    кривого JSON.

    Мутирует переданный ORM-объект напрямую — flush/commit остаются на
    caller'е, как и у остальных гейтов в этом модуле.
    """
    snapshot = server.pre_acs_busy_snapshot
    if snapshot:
        try:
            busy_since_raw = snapshot.get("busy_since")
            server.busy_state = snapshot.get("busy_state") or BusyState.FREE
            server.busy_user_id = snapshot.get("busy_user_id")
            # Снимки, снятые до появления актор-полей, ключей не несут —
            # разворачиваем их в пользовательскую бронь, как было раньше.
            server.busy_actor_type = (
                snapshot.get("busy_actor_type") or BusyActorType.USER
            )
            server.busy_service_name = snapshot.get("busy_service_name")
            server.busy_note = snapshot.get("busy_note")
            server.busy_since = (
                datetime.fromisoformat(busy_since_raw) if busy_since_raw else None
            )
            server.pre_acs_busy_snapshot = None
            return
        except (TypeError, ValueError, AttributeError):
            logger.warning(
                "malformed pre_acs_busy_snapshot on server_id=%s, falling back to free",
                server.id,
            )
    server.busy_state = BusyState.FREE
    server.busy_user_id = None
    server.busy_actor_type = BusyActorType.USER
    server.busy_service_name = None
    server.busy_since = None
    server.busy_note = None
    server.pre_acs_busy_snapshot = None


def ensure_not_acs_locked(identity: IdentityContext, server: Server) -> None:
    """Отбить операцию, пока сервер занят ACS-снимком/восстановлением.

    Бронь `busy_state=acs` системная — обычного владельца у неё нет, поэтому
    пока она держится, менять сервер вправе только администратор
    (`is_server_admin`): department_admin своего отдела или носитель
    service-роли `admin`. Остальным — 409 `SERVER_ACS_BUSY`, тот же код, что
    уже используют гейты dispatch'а create/restore в
    `worker_dispatch._acs_resolve_and_dispatch`.

    Для не-ACS эндпоинтов (power/prepare/account/astra_update/ipmi/
    node_exporter/mgmt-creds/vms_hub и т.п.) — вызывается отдельно каждым
    из них, сюда сама функция никого не гейтит.
    """
    if server.busy_state != BusyState.ACS:
        return
    if is_server_admin(identity, server):
        return
    details = {
        "server_id": server.id,
        "department_id": server.department_id,
        "busy_note": server.busy_note,
    }
    if identity.subject_type is not None:
        details["subject_type"] = identity.subject_type
    audit_service.emit(
        "server.acs_locked",
        target_id=server.id,
        target_type="server",
        status="denied",
        allowed=False,
        details=details,
    )
    raise ConflictError(
        error_code="SERVER_ACS_BUSY",
        message=(
            "Server is locked by an ACS snapshot/restore operation in "
            "progress; only a department/service admin can act on it "
            "until the operation completes"
        ),
        details={"busy_note": server.busy_note},
    )
