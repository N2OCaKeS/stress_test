"""Helper `_dept_guard.assert_dept_admin_target_dept`.

Закрывает дыру silent-bypass: раньше код шёл по паттерну
`if actor and actor.department_id != target: raise`, и при `actor=None`
(race: пользователь удалён после выпуска JWT) проверка молча
пропускала запрос. Helper делает fail-closed.
"""

import pytest

from src.core.exceptions import AuthorizationError
from src.services._dept_guard import assert_dept_admin_target_dept


class _StubUser:
    def __init__(self, department_id: str | None):
        self.department_id = department_id


class _StubUserRepo:
    def __init__(self, user):
        self._user = user
        self.calls: list[str] = []

    async def get_by_id(self, actor_id: str):
        self.calls.append(actor_id)
        return self._user


async def test_actor_none_raises_actor_vanished():
    """actor=None (race-condition после JWT) → 403 ACTOR_VANISHED, не silent-bypass."""
    repo = _StubUserRepo(user=None)
    with pytest.raises(AuthorizationError) as ei:
        await assert_dept_admin_target_dept(
            repo, "usr_ghost", "dept_a",
            error_code="X_DENIED", message="x",
        )
    assert ei.value.error_code == "ACTOR_VANISHED"
    assert ei.value.http_status == 403
    assert repo.calls == ["usr_ghost"]


async def test_actor_in_other_dept_raises_callers_error_code():
    """actor существует, но department_id не совпадает → caller's error_code."""
    repo = _StubUserRepo(user=_StubUser(department_id="dept_b"))
    with pytest.raises(AuthorizationError) as ei:
        await assert_dept_admin_target_dept(
            repo, "usr_da_b", "dept_a",
            error_code="USER_UPDATE_FORBIDDEN",
            message="cannot update outside your dept",
        )
    assert ei.value.error_code == "USER_UPDATE_FORBIDDEN"
    assert ei.value.message == "cannot update outside your dept"


async def test_actor_in_target_dept_passes_silently():
    """actor.department_id == target_dept_id → no raise (возвращает None)."""
    repo = _StubUserRepo(user=_StubUser(department_id="dept_a"))
    result = await assert_dept_admin_target_dept(
        repo, "usr_da_a", "dept_a",
        error_code="X", message="x",
    )
    assert result is None
    # Хелпер должен дернуть get_by_id ровно один раз с переданным actor_id.
    assert repo.calls == ["usr_da_a"]


async def test_actor_dept_none_does_not_silent_pass():
    """`actor.department_id=None` (account_admin без dept) против конкретного target_dept
    — guard зовут только в DA-ветке, но проверка на dept-mismatch обязана сработать.
    """
    repo = _StubUserRepo(user=_StubUser(department_id=None))
    with pytest.raises(AuthorizationError) as ei:
        await assert_dept_admin_target_dept(
            repo, "usr_x", "dept_a",
            error_code="DEPARTMENT_ACCESS_DENIED",
            message="dept mismatch",
        )
    assert ei.value.error_code == "DEPARTMENT_ACCESS_DENIED"
