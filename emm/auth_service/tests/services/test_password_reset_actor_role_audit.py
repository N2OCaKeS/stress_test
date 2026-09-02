"""`actor_role` явно публикуется в details audit-событий

`user.password_reset` (admin-ручка) и `user.self_password_reset`
(self-ручка `/me/password`).

Раньше SIEM-у приходилось доставать тип actor'а через extra-запрос к auth
по `actor_id`. Теперь:

- account_admin / department_admin сбрасывает чужой пароль → `actor_role`
  в details содержит конкретное имя платформенной роли;
- self-reset (`change_own_password`, /me/password) → `actor_role="self"`
  и в success, и в failure (invalid old password);
- legacy-вызов admin-`reset_password` без actor_role: значение
  резолвится из БД (см. cross-dept-guard) и пишется в details.
"""

import pytest

from src.core.constants import PlatformRole
from src.core.exceptions import AuthenticationError
from src.services import audit_service as audit_mod, user_service


def _capture_emits(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


def _pick(events: list[dict], action: str) -> dict:
    for ev in events:
        if ev["action"] == action:
            return ev
    raise AssertionError(f"event {action!r} not emitted; got {[e['action'] for e in events]}")


class TestAdminResetPasswordActorRole:
    async def test_account_admin_sets_account_admin_role(
        self, db, account_admin, user_a, monkeypatch,
    ):
        events = _capture_emits(monkeypatch)
        await user_service.reset_password(
            db,
            actor_id=account_admin.id,
            user_id=user_a.id,
            new_password="NewPass1234!",
            actor_role=PlatformRole.ACCOUNT_ADMIN,
        )
        ev = _pick(events, "user.password_reset")
        assert ev["details"]["actor_role"] == "account_admin"

    async def test_department_admin_sets_department_admin_role(
        self, db, dept_admin_a, user_a, monkeypatch,
    ):
        events = _capture_emits(monkeypatch)
        await user_service.reset_password(
            db,
            actor_id=dept_admin_a.id,
            user_id=user_a.id,
            new_password="NewPass1234!",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
        )
        ev = _pick(events, "user.password_reset")
        assert ev["details"]["actor_role"] == "department_admin"

    async def test_actor_role_resolved_from_db_when_none(
        self, db, account_admin, user_a, monkeypatch,
    ):
        # actor_role не передан → user_service подтянет platform_role из БД
        # (account_admin) и положит её в details.
        events = _capture_emits(monkeypatch)
        await user_service.reset_password(
            db,
            actor_id=account_admin.id,
            user_id=user_a.id,
            new_password="NewPass1234!",
            actor_role=None,
        )
        ev = _pick(events, "user.password_reset")
        assert ev["details"]["actor_role"] == "account_admin"

    async def test_audit_carries_token_revocation_flags(
        self, db, account_admin, user_a, monkeypatch,
    ):
        # Admin-reset отзывает PAT'ы целевого юзера — audit обязан нести
        # честный `tokens_revoked=True` и фактический `pat_revoked_count`,
        # не «PAT сохраняются by design». Создаём два PAT'а до сброса.
        from datetime import timedelta

        from src.repositories.tokens import TokenRepository
        from src.utils.time import utcnow

        token_repo = TokenRepository(db)
        for name in ("pat_one", "pat_two"):
            await token_repo.create(
                user_id=user_a.id,
                name=name,
                token_hash=f"hash_{name}",
                token_prefix=f"dbos_pat_{name}",
                allowed_services=["service_x"],
                expires_at=utcnow() + timedelta(days=30),
            )
        await db.commit()

        events = _capture_emits(monkeypatch)
        await user_service.reset_password(
            db,
            actor_id=account_admin.id,
            user_id=user_a.id,
            new_password="NewPass1234!",
            actor_role=PlatformRole.ACCOUNT_ADMIN,
        )
        ev = _pick(events, "user.password_reset")
        assert ev["details"]["tokens_revoked"] is True
        assert ev["details"]["pat_revoked_count"] == 2

    async def test_actor_equals_target_marked_as_self(
        self, db, account_admin, monkeypatch,
    ):
        # Если admin-ручкой кто-то всё-таки сбрасывает свой же пароль
        # (actor_id == user_id), помечаем "self" вместо роли: SIEM сразу
        # видит self-flow, а не считает это admin-on-other-user действием.
        events = _capture_emits(monkeypatch)
        await user_service.reset_password(
            db,
            actor_id=account_admin.id,
            user_id=account_admin.id,
            new_password="NewSelfPass1234!",
            actor_role=PlatformRole.ACCOUNT_ADMIN,
        )
        ev = _pick(events, "user.password_reset")
        assert ev["details"]["actor_role"] == "self"


class TestSelfPasswordResetActorRole:
    async def test_change_own_password_success_emits_self(
        self, db, user_a, monkeypatch,
    ):
        events = _capture_emits(monkeypatch)
        await user_service.change_own_password(
            db,
            user_id=user_a.id,
            old_password="User12345678!",
            new_password="NewSelf1234!",
        )
        ev = _pick(events, "user.self_password_reset")
        assert ev["details"]["actor_role"] == "self"

    async def test_change_own_password_invalid_old_emits_self(
        self, db, user_a, monkeypatch,
    ):
        events = _capture_emits(monkeypatch)
        with pytest.raises(AuthenticationError) as ei:
            await user_service.change_own_password(
                db,
                user_id=user_a.id,
                old_password="WrongOldPass!",
                new_password="NewSelf1234!",
            )
        assert ei.value.error_code == "INVALID_OLD_PASSWORD"
        ev = _pick(events, "user.self_password_reset")
        assert ev["details"]["actor_role"] == "self"
        assert ev["details"]["reason"] == "invalid_old_password"
