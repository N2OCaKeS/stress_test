"""actor=None при DA-роли → 403 ACTOR_VANISHED во всех 9 call-site'ах.

Service-level: монипатчим `UserRepository.get_by_id`, чтобы вернуть None
для actor_id; проверяем, что каждый из 9 сервисных вызовов поднимает
`AuthorizationError(ACTOR_VANISHED)` вместо silent-pass.
"""

import pytest

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError
from src.repositories.users import UserRepository
from src.services import (
    docker_registry_service,
    oauth_service,
    user_service,
)


@pytest.fixture(autouse=True)
def _force_actor_vanished(monkeypatch):
    """Все вызовы UserRepository.get_by_id с actor_id='usr_ghost' возвращают None."""
    original = UserRepository.get_by_id

    async def patched(self, user_id):
        if user_id == "usr_ghost":
            return None
        return await original(self, user_id)

    monkeypatch.setattr(UserRepository, "get_by_id", patched)


# ── user_service ──────────────────────────────────────────────────────────────


async def test_list_users_by_department_actor_vanished(db, dept_a):
    with pytest.raises(AuthorizationError) as ei:
        await user_service.list_users_by_department(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            department_id=dept_a.id,
        )
    assert ei.value.error_code == "ACTOR_VANISHED"


async def test_create_user_actor_vanished(db, dept_a):
    with pytest.raises(AuthorizationError) as ei:
        await user_service.create_user(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            username="new_x", password="Pass1234!", department_id=dept_a.id,
        )
    assert ei.value.error_code == "ACTOR_VANISHED"


async def test_update_user_actor_vanished(db, user_a):
    # update_user сначала ищет target по user_id, потом actor'а — поэтому
    # user_a реален, а 'usr_ghost' — нет.
    with pytest.raises(AuthorizationError) as ei:
        await user_service.update_user(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            user_id=user_a.id,
            updates={"email": "new@x"},
        )
    assert ei.value.error_code == "ACTOR_VANISHED"


async def test_assign_roles_actor_vanished(db, user_a, service_x):
    with pytest.raises(AuthorizationError) as ei:
        await user_service.assign_roles(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            user_id=user_a.id,
            service_name=service_x.service_name,
            roles=["reader"],
        )
    assert ei.value.error_code == "ACTOR_VANISHED"


# ── oauth_service ────────────────────────────────────────────────────────────


async def test_oauth_create_client_actor_vanished(db, dept_a):
    from src.schemas.oauth import OAuthClientCreate

    data = OAuthClientCreate(
        name="cli_ghost",
        department_id=dept_a.id,
        redirect_uris=["https://example.org/cb"],
        allowed_scopes=["read"],
        grant_types=["authorization_code"],
        is_public=False,
    )
    with pytest.raises(AuthorizationError) as ei:
        await oauth_service.create_client(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            data=data,
        )
    assert ei.value.error_code == "ACTOR_VANISHED"


async def test_oauth_delete_client_actor_vanished(db, dept_a):
    """delete_client: сначала находим client, потом actor."""
    from src.repositories.oauth_clients import OAuthClientRepository

    client_repo = OAuthClientRepository(db)
    client = await client_repo.create(
        department_id=dept_a.id,
        name="cli_delete_test",
        client_secret_hash="hash",
        client_secret_prefix="cs_",
        redirect_uris=["https://example.org/cb"],
        allowed_scopes=["read"],
        grant_types=["authorization_code"],
        description=None,
        is_public=False,
        created_by="usr_seed",
    )
    await db.commit()

    with pytest.raises(AuthorizationError) as ei:
        await oauth_service.delete_client(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            client_db_id=client.id,
        )
    assert ei.value.error_code == "ACTOR_VANISHED"


# ── docker_registry_service ──────────────────────────────────────────────────


async def test_docker_create_config_actor_vanished(db, dept_a):
    from src.models.department_docker_registry import PULL_POLICY_ALL
    from src.schemas.docker_registry import DockerRegistryConfigCreate

    data = DockerRegistryConfigCreate(
        pull_policy=PULL_POLICY_ALL, pull_user_ids=[], push_user_ids=[],
    )
    with pytest.raises(AuthorizationError) as ei:
        await docker_registry_service.create_or_replace_config(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            department_id=dept_a.id,
            data=data,
        )
    assert ei.value.error_code == "ACTOR_VANISHED"


async def test_docker_update_config_actor_vanished(db, dept_a):
    from src.schemas.docker_registry import DockerRegistryConfigUpdate

    with pytest.raises(AuthorizationError) as ei:
        await docker_registry_service.update_config(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            department_id=dept_a.id,
            data=DockerRegistryConfigUpdate(),
        )
    assert ei.value.error_code == "ACTOR_VANISHED"


async def test_docker_delete_config_actor_vanished(db, dept_a):
    with pytest.raises(AuthorizationError) as ei:
        await docker_registry_service.delete_config(
            db, actor_id="usr_ghost",
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
            department_id=dept_a.id,
        )
    assert ei.value.error_code == "ACTOR_VANISHED"
