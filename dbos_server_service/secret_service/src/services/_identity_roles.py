"""Общие role-предикаты по `Identity` для слоя сервисов secret_service.

`access_service` и `credential_service` оба спрашивают одно и то же: «этот
actor — admin secret_service'а? account_admin? чистый guest? может ли admin
действовать над конкретной кред'ой?». Раньше эти четыре функции жили копией
в каждом модуле и успели разъехаться по тексту docstring'ов. Здесь — одна
точка истины; оба модуля реэкспортят нужные имена под своими алиасами.
"""

from __future__ import annotations

from src.core.constants import SERVICE_NAME
from src.dependencies.auth import Identity
from src.models import Credential


def is_service_admin(identity: Identity) -> bool:
    """Носитель `admin`-роли secret_service (без привязки к конкретному dept'у).

    Привязка к dep'у самой кред'ы — отдельный шаг (`is_service_admin_for`),
    потому что admin живёт per-(dept, service) и не имеет cross-dept привилегий.
    """
    return "admin" in identity.roles_for(SERVICE_NAME)


def is_service_admin_for(identity: Identity, cred: Credential) -> bool:
    """`admin` secret_service'а с правом действовать ИМЕННО над `cred`.

    Допустимо, если actor владеет admin-ролью И cred сидит в том же dept'е:
      * `cred.owner_dept_id == identity.department_id` — department / cross_dep;
      * `cred.owner_user_dept_id == identity.department_id` — personal владельца
        из того же dept'а;
      * personal с пустым owner_user_dept_id (старые записи до миграции
        c3b5e7d2a1f8) — допускаем, чтобы admin своего dep'а мог хотя бы
        прочитать аудиторскую креду; жёсткий cut-off потребует backfill'а.
    Cross-dept привилегий у роли нет — admin dep_A не лезет в cred'ы dep_B.
    """
    if not is_service_admin(identity):
        return False
    if identity.department_id is None:
        return False
    if cred.owner_dept_id is not None and cred.owner_dept_id == identity.department_id:
        return True
    if cred.scope == "personal":
        owner_dept = cred.owner_user_dept_id
        if owner_dept is None or owner_dept == identity.department_id:
            return True
    return False


def is_account_admin(identity: Identity) -> bool:
    """Платформенная роль account_admin."""
    return identity.platform_role == "account_admin"


def is_guest_only(identity: Identity) -> bool:
    """Guest = носитель ТОЛЬКО роли `guest` в secret_service.

    Если у actor'а есть ещё какая-то роль (reader/operator/admin) — он не
    guest, идёт обычным путём. Чистый guest получает урезанный listing
    (только id/name/service/scope/visible_to_dept) и больше ничего.
    """
    roles = identity.roles_for(SERVICE_NAME)
    return bool(roles) and all(r == "guest" for r in roles)
