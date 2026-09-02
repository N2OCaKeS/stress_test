"""Fail-closed guard для cross-department-операций department_admin'а.

Историческая схема в сервисах:

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        actor = await user_repo.get_by_id(actor_id)
        if actor and actor.department_id != target_dept_id:
            raise AuthorizationError(...)

Дыра: `actor=None` (race: пользователь удалён уже после выпуска JWT,
но до момента, когда service зовёт `get_by_id`) — `actor and ...`
коротит в False, проверка молча пропускает запрос и DA одного отдела
успевает что-то сделать с ресурсом другого отдела (или просто пройти
там, где должен был получить 403).

Helper делает наоборот: actor обязан существовать; если его нет — 403
с явным error_code, чтобы не отдавать silent-bypass.

Использование (паттерн вызова из service'а):

    from src.services._dept_guard import assert_dept_admin_target_dept

    if actor_role == PlatformRole.DEPARTMENT_ADMIN:
        await assert_dept_admin_target_dept(
            user_repo, actor_id, target_dept_id,
            error_code="DEPARTMENT_ACCESS_DENIED",
            message="department_admin can only X in their own department",
        )

Caller остаётся ответственным за дальнейшие проверки (на `account_admin`
guard не нужен — он работает cross-dept by design; helper зовётся
только в DA-ветке).
"""

from src.core.exceptions import AuthorizationError
from src.repositories.users import UserRepository


async def assert_dept_admin_target_dept(
    user_repo: UserRepository,
    actor_id: str,
    target_dept_id: str | None,
    *,
    error_code: str,
    message: str,
) -> None:
    """Убедиться, что DA-actor существует и принадлежит target_dept_id.

    Бросает `AuthorizationError`:

    * `error_code="ACTOR_VANISHED"` — actor отсутствует (race-condition
      между issue JWT и check'ом). HTTP 403; смысл — fail-closed.
    * `error_code=<caller's>` — actor есть, но dept не совпадает.

    Про `target_dept_id=None`: сигнатура допускает None, но любой actor
    в БД имеет непустой `department_id`, поэтому сравнение
    `actor.department_id != None` для DA всегда даст 403 с caller-овым
    `error_code`. Это безопасный дефолт: если caller случайно прокинул
    None, запрос отбрасывается, а не пропускается. На практике None сюда
    не доходит — все вызовы передают `target_dept_id` из Pydantic-схем,
    где поле объявлено как Required `str` (см. OAuth/User/Docker-registry
    схемы). None-ветка оставлена как defensive guard на случай будущих
    caller'ов, которые могут принимать опциональный dept.
    """
    actor = await user_repo.get_by_id(actor_id)
    if actor is None:
        raise AuthorizationError(
            error_code="ACTOR_VANISHED",
            message="Actor no longer exists",
        )
    if actor.department_id != target_dept_id:
        raise AuthorizationError(
            error_code=error_code,
            message=message,
        )


async def assert_actor_exists(
    user_repo: UserRepository,
    actor_id: str,
):
    """Read-side зеркало `assert_dept_admin_target_dept`.

    Write-операции для DEPARTMENT_ADMIN уже бросают `ACTOR_VANISHED` через
    `assert_dept_admin_target_dept`. Read-операции с тем же race-окном
    (`actor = await user_repo.get_by_id(actor_id); actor.department_id`)
    должны вести себя симметрично, а не отдавать тихий пустой список.

    Возвращает actor (на случай, если caller сам хочет дальше работать
    с `actor.department_id`), чтобы не делать второй SELECT.
    """
    actor = await user_repo.get_by_id(actor_id)
    if actor is None:
        raise AuthorizationError(
            error_code="ACTOR_VANISHED",
            message="Actor no longer exists",
        )
    return actor
