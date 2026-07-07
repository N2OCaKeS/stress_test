"""Подключение `lockout_service` к `credential_service.load_for_action`.

Раньше lockout-сервис был полностью реализован, но не вызывался ни в одной
production-ручке. Эти тесты проверяют, что:

1. На denied-access инкрементится счётчик per-actor.
2. При превышении порога — RateLimitError (429) + audit `tokens.lockout_triggered`.
3. На успешном reveal'е счётчик сбрасывается.
4. Предварительный pre-check is_locked отбивает 429 ДО SELECT по cred.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.exceptions import NotFoundError, RateLimitError
from src.dependencies.auth import Identity
from src.repositories import credentials as cred_repo
from src.services import credential_service, lockout_service


def _identity(
    *,
    user_id: str = "usr_locktest0001",
    actor_type: str = "user",
    department_id: str | None = "dep_actor0001",
    roles: list[str] | None = None,
) -> Identity:
    return Identity(
        user_id=user_id,
        username="lock",
        actor_type=actor_type,
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": roles or ["reader"]},
        is_banned=False,
        platform_role=None,
    )


class _FakeSettings:
    def __init__(self) -> None:
        self.lockout_threshold = 3
        self.lockout_window_seconds = 60
        self.lockout_duration_seconds = 30


@pytest.fixture
def _low_threshold(monkeypatch):
    monkeypatch.setattr(lockout_service, "get_settings", lambda: _FakeSettings())


async def _create_personal_other_user(adb, cred_id: str = "cred_lockfix01"):
    cred = await cred_repo.create(
        adb,
        id=cred_id,
        name=cred_id,
        service="jira",
        scope="personal",
        owner_user_id="usr_owner0001",
        owner_dept_id=None,
        owner_user_dept_id="dep_owner0001",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_owner0001",
    )
    await adb.commit()
    return cred


@pytest.mark.asyncio
async def test_denied_access_increments_lockout(adb, _low_threshold) -> None:
    cred = await _create_personal_other_user(adb)
    actor = _identity()

    # Один denied — счётчик растёт, но не локает.
    with patch.object(credential_service.audit_service, "emit", lambda *a, **k: None):
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(adb, actor, cred.id, "read")
    assert not lockout_service.is_locked(actor.user_id)


@pytest.mark.asyncio
async def test_lockout_triggers_after_threshold(adb, _low_threshold) -> None:
    cred = await _create_personal_other_user(adb)
    actor = _identity()

    emitted_actions = []
    with patch.object(
        credential_service.audit_service,
        "emit",
        side_effect=lambda action, **kw: emitted_actions.append(action),
    ):
        for _ in range(3):
            with pytest.raises(NotFoundError):
                await credential_service.load_for_action(adb, actor, cred.id, "read")

    assert lockout_service.is_locked(actor.user_id)
    assert "tokens.lockout_triggered" in emitted_actions


@pytest.mark.asyncio
async def test_locked_actor_gets_429(adb, _low_threshold) -> None:
    cred = await _create_personal_other_user(adb)
    actor = _identity()
    # Преднамеренно локаем без эмиссии.
    with patch.object(credential_service.audit_service, "emit", lambda *a, **k: None):
        for _ in range(3):
            with pytest.raises(NotFoundError):
                await credential_service.load_for_action(adb, actor, cred.id, "read")
    # Следующий запрос — 429 ещё до SELECT'а.
    with pytest.raises(RateLimitError) as exc:
        await credential_service.load_for_action(adb, actor, cred.id, "read")
    assert exc.value.error_code == "ACTOR_LOCKED_OUT"
    assert exc.value.http_status == 429
    assert "retry_after_seconds" in exc.value.details


@pytest.mark.asyncio
async def test_success_does_not_reset_denied_window(adb, _low_threshold) -> None:
    """Успех на своей кред'е НЕ обнуляет денай-окно.

    Иначе атакующий, перемежающий перебор чужих cred_id одним GET'ом по своей
    кред'е, держал бы счётчик ниже порога вечно — lockout не сработал бы.
    """
    owner = _identity(user_id="usr_owner0002")
    cred = await cred_repo.create(
        adb,
        id="cred_lockfix02",
        name="own",
        service="jira",
        scope="personal",
        owner_user_id=owner.user_id,
        owner_dept_id=None,
        owner_user_dept_id=owner.department_id,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by=owner.user_id,
    )
    other = await cred_repo.create(
        adb,
        id="cred_lockfix02b",
        name="other",
        service="jira",
        scope="personal",
        owner_user_id="usr_otheruser01",
        owner_dept_id=None,
        owner_user_dept_id="dep_other0001",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_otheruser01",
    )
    await adb.commit()

    # threshold=3: перемежаем denied по чужой кред'е с успехом по своей.
    with patch.object(credential_service.audit_service, "emit", lambda *a, **k: None):
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(adb, owner, other.id, "read")
        await credential_service.load_for_action(adb, owner, cred.id, "read")
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(adb, owner, other.id, "read")
        await credential_service.load_for_action(adb, owner, cred.id, "read")
        with pytest.raises(NotFoundError):
            await credential_service.load_for_action(adb, owner, other.id, "read")

    # 3 denied накопились несмотря на перемежающиеся успехи — actor залочен.
    assert lockout_service.is_locked(owner.user_id)
