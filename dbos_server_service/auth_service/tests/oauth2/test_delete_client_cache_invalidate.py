"""Тесты: `oauth_service.delete_client` сбрасывает identity-кэш клиента.

Симметрия с user-side ban/role-change: без сброса старый client_credentials
JWT продолжает проходить `get_current_identity` до истечения TTL
(~5s по умолчанию), и soft-deactivate'нутый клиент де-факто остаётся
активным в окне TTL.

Кэш ключуется по `IdentityContext.user_id`, а для m2m туда уезжает
`client.client_id` — соответственно invalidate'ить нужно по `client_id`,
а не по DB-id или name.
"""

import pytest

from src.services import oauth_service
from src.schemas.oauth import OAuthClientCreate


@pytest.fixture
def spy_invalidate(monkeypatch):
    """Перехват общего `_cache_invalidation.invalidate_identity_cache`.

    Подменяем и в общем модуле, и в alias'е внутри `oauth_service` — на
    случай, если кто-то заведёт локальную копию хелпера с lazy import.
    """
    seen: list[str] = []

    def spy(subject_id: str) -> None:
        seen.append(subject_id)

    from src.services import _cache_invalidation, oauth_service as oauth_mod

    monkeypatch.setattr(_cache_invalidation, "invalidate_identity_cache", spy)
    monkeypatch.setattr(oauth_mod, "_invalidate_identity_cache", spy)
    return seen


async def test_delete_client_invalidates_identity_cache(
    db, account_admin, dept_a, spy_invalidate,
):
    """`delete_client` после commit'а сбрасывает кэш по `client.client_id`."""
    created = await oauth_service.create_client(
        db,
        actor_id=account_admin.id,
        actor_role="account_admin",
        data=OAuthClientCreate(
            department_id=dept_a.id,
            name="cache_invalidate_target",
            grant_types=["client_credentials"],
            redirect_uris=[],
            allowed_scopes=[],
        ),
    )
    spy_invalidate.clear()

    await oauth_service.delete_client(
        db,
        actor_id=account_admin.id,
        actor_role="account_admin",
        client_db_id=created.id,
    )

    # Кэш-ключ — `client_id` (`cli_*`), не `id` (`oac_*`).
    assert created.client_id in spy_invalidate
    assert created.id not in spy_invalidate


async def test_delete_client_uses_shared_helper():
    """`oauth_service._invalidate_identity_cache` — это тот же объект, что и
    общий `_cache_invalidation.invalidate_identity_cache`. Защита от регрессии
    в духе локальной копии с try/except.
    """
    from src.services import _cache_invalidation

    assert (
        oauth_service._invalidate_identity_cache
        is _cache_invalidation.invalidate_identity_cache
    )
