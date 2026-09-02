"""Юнит-тесты: общий `_invalidate_identity_cache` хелпер.

Три сервиса (`user_service`, `group_service`, `service_role_service`)
раньше дублировали одну и ту же функцию с lazy import +
`try/except ImportError`. После дедупа все импортят
`src.services._cache_invalidation.invalidate_identity_cache`, который через
`dependencies.auth` зовёт `invalidate_identity_cache_for_user`.

Тест проверяет, что:
1. Хелпер действительно делегирует в `dependencies.auth`.
2. Все три сервиса используют именно этот хелпер (`is`-identity по объекту
   функции — `_invalidate_identity_cache` в каждом сервисе должен быть тем же
   `invalidate_identity_cache` из общего модуля).
"""

from src.services import _cache_invalidation, group_service, service_role_service, user_service


def test_helper_delegates_to_dependencies_auth(monkeypatch):
    """`invalidate_identity_cache(user_id)` должен звать
    `dependencies.auth.invalidate_identity_cache_for_user(user_id)`.
    """
    from src.dependencies import auth as deps_auth_mod

    seen: list[str] = []

    def spy(user_id: str) -> int:
        seen.append(user_id)
        return 0

    monkeypatch.setattr(deps_auth_mod, "invalidate_identity_cache_for_user", spy)
    _cache_invalidation.invalidate_identity_cache("usr_test_123")
    assert seen == ["usr_test_123"]


def test_all_three_services_use_shared_helper():
    """`_invalidate_identity_cache` в user/group/service_role — один и тот же
    объект (общий хелпер). Защита от регрессии «кто-то снова завёл локальную
    копию с try/except».
    """
    shared = _cache_invalidation.invalidate_identity_cache
    assert user_service._invalidate_identity_cache is shared
    assert group_service._invalidate_identity_cache is shared
    assert service_role_service._invalidate_identity_cache is shared
