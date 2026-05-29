"""Unit-тесты `src/core/exceptions.py` и базовых веток `dependencies/auth`."""

from __future__ import annotations

import secrets

import pytest

from src.core.exceptions import (
    AppException,
    AuthenticationError,
    AuthorizationError,
    DomainValidationError,
    NotFoundError,
)
from src.dependencies import auth as auth_dep


# ── http_status каждого подкласса ────────────────────────────────────────────

class TestExceptionStatuses:
    @pytest.mark.parametrize("cls, expected", [
        (AppException, 500),
        (AuthenticationError, 401),
        (AuthorizationError, 403),
        (NotFoundError, 404),
        (DomainValidationError, 422),
    ])
    def test_default_http_status(self, cls, expected: int):
        exc = cls(error_code="X", message="m")
        assert exc.http_status == expected

    def test_can_override_status(self):
        exc = NotFoundError(error_code="X", message="m", http_status=499)
        assert exc.http_status == 499

    def test_details_default_empty_dict(self):
        exc = AppException(error_code="X", message="m")
        assert exc.details == {}

    def test_details_can_be_populated(self):
        exc = AppException(error_code="X", message="m", details={"k": "v"})
        assert exc.details == {"k": "v"}

    def test_details_default_factory_isolated(self):
        """Каждый экземпляр должен иметь свой dict — иначе изменения в одном
        протекают в другой (`dataclasses.field(default_factory=dict)`)."""
        a = AppException(error_code="A", message="m")
        b = AppException(error_code="B", message="m")
        a.details["x"] = 1
        assert b.details == {}

    def test_subclass_preserves_dataclass_fields(self):
        exc = AuthenticationError(
            error_code="INVALID_TOKEN", message="bad", details={"reason": "expired"},
        )
        assert exc.error_code == "INVALID_TOKEN"
        assert exc.details == {"reason": "expired"}
        assert exc.http_status == 401


# ── require_service_token ────────────────────────────────────────────────────


def _fake_request(headers: dict | None = None):
    """Minimal FastAPI ``Request`` stand-in for ``require_service_token``.

    The dependency now reads ``X-Service-Identity`` from ``request.headers`` and
    stashes the verified value on ``request.state.service_identity``. Tests in
    this file don't exercise the identity-validation path (separate file
    ``test_services.py::TestRegisterEventsServiceIdentityGuard``), so a no-op
    namespace with an empty headers dict is enough.
    """
    from types import SimpleNamespace
    return SimpleNamespace(headers=headers or {}, state=SimpleNamespace(), url=SimpleNamespace(path="/"))


class TestRequireServiceToken:
    def test_missing_credentials_returns_401(self):
        with pytest.raises(AppException) as exc:
            auth_dep.require_service_token(request=_fake_request(), credentials=None)
        assert exc.value.error_code == "INVALID_SERVICE_TOKEN"
        assert exc.value.http_status == 401

    def test_wrong_token_returns_401(self, monkeypatch):
        from src.core.config import get_settings
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setenv("SERVICE_API_KEY", "expected-key-xyz")

        class _Creds:
            credentials = "wrong-key"

        with pytest.raises(AppException) as exc:
            auth_dep.require_service_token(request=_fake_request(), credentials=_Creds())
        assert exc.value.error_code == "INVALID_SERVICE_TOKEN"

    def test_correct_token_passes_silently(self, monkeypatch):
        from src.core.config import get_settings
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setenv("SERVICE_API_KEY", "shared-svc-key")

        class _Creds:
            credentials = "shared-svc-key"

        # должно вернуть None без исключений
        assert auth_dep.require_service_token(request=_fake_request(), credentials=_Creds()) is None

    def test_timing_safe_compare_used(self):
        """`secrets.compare_digest` — устойчив к timing атакам.
        Юнит-проверка только что используется правильная функция."""
        import inspect
        src = inspect.getsource(auth_dep.require_service_token)
        assert "secrets.compare_digest" in src


# ── _fetch_identity error branches (network) ─────────────────────────────────

class _FakeRequest:
    def __init__(self):
        from types import SimpleNamespace
        self.state = SimpleNamespace()


def _run(coro):
    """Drive an async coroutine in a sync test. ``_fetch_identity`` became
    ``async def`` in the introspect-pool fix."""
    import asyncio
    return asyncio.run(coro)


class TestFetchIdentityNetworkErrors:
    """Pool is reset to ``None`` in each test so the fallback (per-call
    ``httpx.post``) path is exercised — that's where ``monkeypatch.setattr
    (httpx, "post", ...)`` lands. The pooled path is covered by
    ``test_introspect_pool.py``.
    """

    def test_missing_credentials_returns_401(self, monkeypatch):
        monkeypatch.setattr(auth_dep, "_introspect_client", None)
        with pytest.raises(AppException) as exc:
            _run(auth_dep._fetch_identity(None, _FakeRequest()))
        assert exc.value.error_code == "MISSING_TOKEN"

    def test_no_auth_service_url_returns_503(self, monkeypatch):
        from src.core.config import get_settings
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setattr(auth_dep, "_introspect_client", None)
        monkeypatch.setenv("AUTH_SERVICE_URL", "")
        monkeypatch.setenv("SERVICE_API_KEY", "x")

        class _Creds:
            credentials = "tok"

        with pytest.raises(AppException) as exc:
            _run(auth_dep._fetch_identity(_Creds(), _FakeRequest()))
        assert exc.value.error_code == "AUTH_SERVICE_NOT_CONFIGURED"
        assert exc.value.http_status == 503

    def test_timeout_returns_503(self, monkeypatch, mock_introspect):
        import httpx
        from src.core.config import get_settings
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth")
        monkeypatch.setenv("SERVICE_API_KEY", "x")
        class _Creds:
            credentials = "tok"

        with mock_introspect(side_effect=httpx.TimeoutException("slow")):
            with pytest.raises(AppException) as exc:
                _run(auth_dep._fetch_identity(_Creds(), _FakeRequest()))
        assert exc.value.error_code == "AUTH_SERVICE_TIMEOUT"

    def test_connect_error_returns_503(self, monkeypatch, mock_introspect):
        import httpx
        from src.core.config import get_settings
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth")
        monkeypatch.setenv("SERVICE_API_KEY", "x")
        class _Creds:
            credentials = "tok"

        with mock_introspect(side_effect=httpx.ConnectError("nope")):
            with pytest.raises(AppException) as exc:
                _run(auth_dep._fetch_identity(_Creds(), _FakeRequest()))
        assert exc.value.error_code == "AUTH_SERVICE_UNREACHABLE"

    def test_non_200_response_returns_503(self, monkeypatch):
        from src.core.config import get_settings
        import httpx
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setattr(auth_dep, "_introspect_client", None)
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth")

        class _R:
            status_code = 500
            def json(self): return {}

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: _R())
        class _Creds:
            credentials = "tok"

        with pytest.raises(AppException) as exc:
            _run(auth_dep._fetch_identity(_Creds(), _FakeRequest()))
        assert exc.value.error_code == "AUTH_SERVICE_ERROR"

    def test_inactive_token_returns_401(self, monkeypatch, mock_introspect):
        from src.core.config import get_settings
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth")
        class _Creds:
            credentials = "tok"

        with mock_introspect(json_body={"active": False}):
            with pytest.raises(AppException) as exc:
                _run(auth_dep._fetch_identity(_Creds(), _FakeRequest()))
        assert exc.value.error_code == "INVALID_TOKEN"

    def test_banned_user_returns_401(self, monkeypatch, mock_introspect):
        from src.core.config import get_settings
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth")
        class _Creds:
            credentials = "tok"

        with mock_introspect(json_body={"active": True, "is_banned": True, "sub": "u"}):
            with pytest.raises(AppException) as exc:
                _run(auth_dep._fetch_identity(_Creds(), _FakeRequest()))
        assert exc.value.error_code == "USER_BANNED"

    def test_active_token_stores_identity_on_request_state(self, monkeypatch, mock_introspect):
        from src.core.config import get_settings
        get_settings.cache_clear()  # type: ignore[attr-defined]
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth")
        req = _FakeRequest()
        class _Creds:
            credentials = "tok"

        body = {
            "active": True, "is_banned": False, "sub": "usr_1",
            "platform_role": "loging_admin",
            "service_roles": {"loging_service": ["reader"]},
        }
        with mock_introspect(json_body=body):
            identity = _run(auth_dep._fetch_identity(_Creds(), req))
        assert identity["user_id"] == "usr_1"
        assert identity["_loging_service_roles"] == ["reader"]
        assert req.state.auth_identity["sub"] == "usr_1"
