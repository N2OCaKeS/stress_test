"""Unit-тесты для actor_type != "user" и != "oauth_client" в get_current_identity.

При actor_type="bot" / "service" / "unknown" / пустая строка — должен вернуться 401
ACCESS_TOKEN_EXPIRED (не 500, не 403). Это ветка else в get_current_identity:
  > Прочие actor_type — reject.

Дополнительно: require_user_context отвергает oauth_client-identity.
"""

import time
from datetime import timedelta

import pytest

from src.core.config import get_settings
from src.core.exceptions import AuthenticationError, AuthorizationError
from src.core.security import create_access_token
from src.dependencies import auth as auth_dep
from src.schemas.auth import IdentityContext

import jwt as _jwt_lib


def _make_request(headers: dict | None = None):
    from fastapi import Request
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": b"",
    }
    return Request(scope)


def _forge_token_with_actor_type(actor_type: str) -> str:
    """Создать JWT с нашим secret_key, но нестандартным actor_type."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": "usr_test",
        "actor_type": actor_type,
        "iat": now,
        "exp": now + 600,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return _jwt_lib.encode(payload, settings.secret_key, algorithm="HS256")


# ── Неизвестный actor_type → 401 ─────────────────────────────────────────────

class TestUnknownActorType:
    """Любой actor_type, кроме 'user' и 'oauth_client', должен отбиваться как 401.

    DB-слой не вызывается (декод проходит, но dispatch'а на handler нет).
    """

    @pytest.mark.parametrize("actor_type", [
        "bot",
        "service",
        "system",
        "unknown_type",
        "ADMIN",
        "  ",
        "0",
    ])
    async def test_unknown_actor_type_raises_authentication_error(self, actor_type):
        token = _forge_token_with_actor_type(actor_type)
        req = _make_request({"Authorization": f"Bearer {token}"})
        with pytest.raises(AuthenticationError) as exc:
            # db=None — до DB не дойдём, dispatch срабатывает раньше
            await auth_dep.get_current_identity(req, db=None)
        assert exc.value.error_code == "ACCESS_TOKEN_EXPIRED"


# ── require_user_context: oauth_client → 403 ─────────────────────────────────

class TestRequireUserContext:
    def _oauth_identity(self) -> IdentityContext:
        return IdentityContext(
            user_id="cli_abc",
            username="test_client",
            subject_type="oauth_client",
        )

    def _user_identity(self) -> IdentityContext:
        return IdentityContext(
            user_id="usr_abc",
            username="alice",
            subject_type="user",
        )

    def test_oauth_client_rejected(self):
        identity = self._oauth_identity()
        with pytest.raises(AuthorizationError) as exc:
            auth_dep.require_user_context(identity)
        assert exc.value.error_code == "USER_CONTEXT_REQUIRED"

    def test_user_identity_passes(self):
        identity = self._user_identity()
        result = auth_dep.require_user_context(identity)
        assert result is identity

    def test_none_subject_type_treated_as_user(self):
        """subject_type=None (default) — require_user_context должен пропустить.

        IdentityContext.subject_type default = "user" (backward compat).
        """
        identity = IdentityContext(user_id="usr_x", username="x")
        # default subject_type is "user"
        assert identity.subject_type == "user"
        result = auth_dep.require_user_context(identity)
        assert result is identity

    def test_empty_subject_type_rejected_by_pydantic(self):
        """Пустая строка / невалидное значение subject_type режется Pydantic'ом
        до того, как identity дойдёт до handler-а.

        После перевода `IdentityContext.subject_type` на `SubjectType`-enum
        конструктор сам валидирует входное значение — отдельной проверки
        в `require_user_context` уже не нужно. Раньше fallback на str принимал
        что угодно и отбивался уже на guard'е.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            IdentityContext(
                user_id="usr_y",
                username="y",
                subject_type="",
            )

    def test_oauth_client_subject_type_passes_validation(self):
        """`subject_type="oauth_client"` корректно коэрсится в `SubjectType.OAUTH_CLIENT`."""
        from src.core.constants import SubjectType

        identity = IdentityContext(
            user_id="cli_x",
            username="x",
            subject_type="oauth_client",
        )
        assert identity.subject_type == SubjectType.OAUTH_CLIENT


# ── identity_cache_put / _get с TTL ──────────────────────────────────────────

class TestIdentityCache:
    """Проверяем что cache-miss на unknown actor_type не кэшируется."""

    def test_unknown_actor_type_not_cached_after_failure(self):
        """Если auth провалился до _identity_cache_put — кэш не должен содержать entry."""
        auth_dep._identity_cache_clear()
        token = _forge_token_with_actor_type("bot")
        key = auth_dep._token_cache_key(token)
        assert auth_dep._identity_cache.get(key) is None

    def test_cache_put_and_get_with_valid_identity(self, monkeypatch):
        """_identity_cache_put + _identity_cache_get базовый round-trip."""
        import src.dependencies.auth as _a
        monkeypatch.setattr(_a, "_IDENTITY_CACHE_TTL_SECONDS", 60.0)
        _a._identity_cache_clear()

        identity = IdentityContext(user_id="usr_cache", username="cached")
        fake_token = "fake.token.value"
        _a._identity_cache_put(fake_token, identity)
        cached = _a._identity_cache_get(fake_token)
        assert cached is not None
        assert cached.user_id == "usr_cache"
        _a._identity_cache_clear()

    def test_cache_returns_none_after_ttl_expires(self, monkeypatch):
        """TTL=0 → кэш disabled → всегда None."""
        import src.dependencies.auth as _a
        monkeypatch.setattr(_a, "_IDENTITY_CACHE_TTL_SECONDS", 0.0)
        _a._identity_cache_clear()

        identity = IdentityContext(user_id="usr_ttl", username="ttltest")
        _a._identity_cache_put("some_token", identity)
        # TTL=0 → _identity_cache_put is no-op
        assert _a._identity_cache_get("some_token") is None
