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
