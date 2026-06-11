"""auth: симметрия cache invalidation в auto-unban, fail-closed
в `create_pat`, и порядок dept-guard в `assign_roles`.

Три зоны:
  1. `auto_unban_if_expired` должен сбросить identity-cache по `user.id`,
     ровно как manual `unban_user`. До фикса JIT auto-unban на login оставлял
     `is_banned=True` в кэше до TTL.
  2. `token_service.create_pat` с актором, исчезнувшим между issue JWT и
     call'ом, скипал dept-scope валидацию. Должен бросать
     `AuthorizationError(ACTOR_VANISHED)`.
  3. `assign_roles` под DA из dept_alpha по target из dept_beta должен
     отбиваться `USER_ROLE_UPDATE_FORBIDDEN` ДО `has_active_access`-чека,
     иначе error_code'ы (`SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` vs
     `INVALID_SERVICE_ROLE`) леквают наличие сервиса/ролей в чужом отделе.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import update

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError
from src.models import Ban
from src.repositories.users import UserRepository
from src.services import token_service, user_service


# ── Fix 1: auto_unban_if_expired сбрасывает identity-cache ───────────────────


class TestAutoUnbanInvalidatesIdentityCache:
    async def test_auto_unban_calls_invalidate_identity_cache(
        self, client, admin_token, user_a, db, monkeypatch,
    ):
        """auto_unban_if_expired должен дёрнуть
        `invalidate_identity_cache_for_user(user.id)` после `db.commit()`."""
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await client.post(
            f"/api/auth/v1/users/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "auto-cache", "expires_at": future},
        )
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()

        from src.dependencies import auth as deps_auth_mod

        seen: list[str] = []
        original = deps_auth_mod.invalidate_identity_cache_for_user

        def spy(uid: str) -> int:
            seen.append(uid)
            return original(uid)

        monkeypatch.setattr(deps_auth_mod, "invalidate_identity_cache_for_user", spy)

        await db.refresh(user_a)
        result = await user_service.auto_unban_if_expired(db, user_a)
        assert result is True
        assert user_a.id in seen, (
            f"auto_unban_if_expired должен сбросить identity-cache для "
            f"user.id={user_a.id}, got calls: {seen}"
        )

    async def test_login_after_auto_unban_returns_is_banned_false(
        self, client, admin_token, user_a, db,
    ):
        """E2E: ban → expire → login → `/me` должен сразу видеть
        `is_banned=False` без ожидания TTL identity-cache.

        Без сброса кэша внутри auto-unban предыдущий cache-entry (с предыдущей
        попытки login'а во время ban'а) мог бы держать `is_banned=True`. Здесь
        предварительной попытки нет, но проверяем, что login-response отражает
        свежий state — это тот же путь, по которому пойдёт `/me`.
        """
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await client.post(
            f"/api/auth/v1/users/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "me-after-unban", "expires_at": future},
        )
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()

        login = await client.post(
            "/api/auth/v1/login",
            json={"username": "t_user_a", "password": "User12345678!"},
        )
        assert login.status_code == 200, login.text
        body = login.json()
        assert body["identity"]["is_banned"] is False

        # Прямой `/me` с свежим access-токеном — тоже должен видеть not banned.
        access = body["access_token"]
        me = await client.get(
            "/api/auth/v1/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert me.status_code == 200, me.text
        assert me.json()["is_banned"] is False


# ── Fix 2: create_pat fail-closed при actor=None ─────────────────────────────


class TestCreatePATActorVanished:
    async def test_actor_vanished_raises_authorization_error(
        self, db, user_a, monkeypatch,
    ):
        """`UserRepository.get_by_id(actor_id) → None` (race delete) при
        непустом `allowed_services` — `AuthorizationError(ACTOR_VANISHED)`."""
        original = UserRepository.get_by_id

        async def patched(self, uid):
            if uid == "usr_ghost":
                return None
            return await original(self, uid)

        monkeypatch.setattr(UserRepository, "get_by_id", patched)

        with pytest.raises(AuthorizationError) as ei:
            await token_service.create_pat(
                db,
                actor_id="usr_ghost",
                name="ghost_pat",
                allowed_services=["any_service"],
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
        assert ei.value.error_code == "ACTOR_VANISHED"

    async def test_empty_allowed_services_skips_actor_lookup(
        self, db, user_a, monkeypatch,
    ):
        """`allowed_services=[]` — валидации scope не нужно, `get_by_id`
        вообще не зовётся; ACTOR_VANISHED не срабатывает даже для
        несуществующего actor'а в этом конкретном пути. Эта ветка
        дублирует `test_pat_create_edge.test_empty_allowed_services_skips_scope_check`,
        но сюда добавлена явная проверка: spy на get_by_id не вызывался.
        """
        calls: list[str] = []
        original = UserRepository.get_by_id

        async def patched(self, uid):
            calls.append(uid)
            return await original(self, uid)

        monkeypatch.setattr(UserRepository, "get_by_id", patched)
        pat = await token_service.create_pat(
            db, actor_id=user_a.id, name="empty_scope_no_actor_lookup",
            allowed_services=[],
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        assert pat.token.startswith("dbos_pat_")
        assert calls == [], (
            f"create_pat с пустым scope не должен звать user_repo.get_by_id, "
            f"got: {calls}"
        )


# ── Fix 3: assign_roles — dept-isolation ВПЕРЕДИ has_active_access ──────────


class TestAssignRolesDeptIsolationFirst:
    async def test_da_cross_dept_target_returns_forbidden_before_service_check(
        self, db, dept_admin_a, user_b, service_x,
    ):
        """DA из dept_alpha вызывает assign_roles для user_b (dept_beta) с
        service_x. dept_beta не имеет доступа к service_x. До фикса
        `has_active_access(dept_beta, service_x)` срабатывал первым и отдавал
        `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT` — DA узнавал, что у dept_beta нет
        service_x. После фикса `USER_ROLE_UPDATE_FORBIDDEN` приходит раньше,
        и oracle закрыт.
        """
        with pytest.raises(AuthorizationError) as ei:
            await user_service.assign_roles(
                db,
                actor_id=dept_admin_a.id,
                actor_role=PlatformRole.DEPARTMENT_ADMIN,
                user_id=user_b.id,
                service_name=service_x.service_name,
                roles=["reader"],
            )
        assert ei.value.error_code == "USER_ROLE_UPDATE_FORBIDDEN"

    async def test_da_cross_dept_target_with_service_in_target_dept_still_forbidden(
        self, db, dept_admin_a, user_b, dept_b_with_service, service_y,
    ):
        """То же самое, но в target-dept сервис ЕСТЬ. До фикса
        `has_active_access` пропускал, дальше `role_def_repo.exists` находил
        существующие роли, и dept-guard в конце говорил forbidden. Но если бы
        ролей не было — error_code был бы `INVALID_SERVICE_ROLE`, а не
        forbidden, и DA отличал бы один state от другого. Здесь проверяем,
        что независимо от состояния target-dept'а — guard срабатывает раньше.
        """
        with pytest.raises(AuthorizationError) as ei:
            await user_service.assign_roles(
                db,
                actor_id=dept_admin_a.id,
                actor_role=PlatformRole.DEPARTMENT_ADMIN,
                user_id=user_b.id,
                service_name=service_y.service_name,
                roles=["reader"],
            )
        assert ei.value.error_code == "USER_ROLE_UPDATE_FORBIDDEN"

    async def test_da_same_dept_target_still_works(
        self, db, dept_admin_a, user_a, service_x,
    ):
        """Sanity: DA из dept_alpha на user_a (dept_alpha) — должен пройти
        guard и доехать до set_roles. Никакой регрессии happy-path'а."""
        await user_service.assign_roles(
            db,
            actor_id=dept_admin_a.id,
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            user_id=user_a.id,
            service_name=service_x.service_name,
            roles=["reader"],
        )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def service_y(db):
    from tests.conftest import _make_service
    return await _make_service(db, "service_y")


@pytest_asyncio.fixture()
async def dept_b_with_service(db, dept_b, service_y):
    from tests.conftest import _grant_service
    await _grant_service(db, dept_b.id, service_y.service_name)
    return dept_b
