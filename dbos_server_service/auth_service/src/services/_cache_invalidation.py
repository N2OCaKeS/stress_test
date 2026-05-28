"""Общий хелпер для сброса identity-кэша после privilege-changing операций.

Сервисы (`user_service`, `group_service`, `service_role_service` и т.п.)
дёргают `invalidate_identity_cache` после ban/unban/role-change/membership
update — чтобы изменения вступали в силу до истечения TTL.

Импорт `dependencies.auth` оставлен ленивым: `dependencies.auth` тянет
`services.auth_service` → audit/context, а сервисы импортируются раньше
зависимостей. Прямой top-level импорт даст цикл.
"""


def invalidate_identity_cache(user_id: str) -> None:
    """Сбросить identity-кэш юзера.

    Lazy import, swallow `ImportError` — на ранних bootstrap/test-импортах
    модуль `dependencies.auth` может быть ещё не загружен, и тогда сбрасывать
    нечего.
    """
    try:
        from src.dependencies import auth as deps_auth
    except ImportError:
        return
    deps_auth.invalidate_identity_cache_for_user(user_id)
