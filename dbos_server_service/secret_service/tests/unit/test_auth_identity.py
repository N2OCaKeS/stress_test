"""Тесты `dependencies/auth.py` — introspect, кэш, guard helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ServiceUnavailableError,
)
from src.dependencies import auth as auth_dep


# Перебиваем `tests/unit/conftest.py::_create_schema` — наш тест не трогает БД.
@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    yield


def _mk_request(token: str | None) -> MagicMock:
    """Минимальный stub Request с заголовком Authorization."""
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = MagicMock()
    request.headers = headers
    return request


def _introspect_body(**overrides) -> dict:
    body = {
        "active": True,
        "sub": "usr_abc",
        "username": "alice",
        "subject_type": "user",
        "department_id": "dep_1",
        "allowed_services": ["secret_service"],
        "service_roles": {"secret_service": ["reader"]},
        "is_banned": False,
        "platform_role": None,
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _clear_cache():
    auth_dep._cache_clear_for_tests()
    yield
    auth_dep._cache_clear_for_tests()


# ── shape checks / missing bearer ────────────────────────────────────────────


async def test_missing_bearer_raises_401():
    with pytest.raises(AuthenticationError) as exc:
        await auth_dep.get_identity(_mk_request(None))
    assert exc.value.error_code == "ACCESS_TOKEN_MISSING"


async def test_invalid_shape_token_raises_401_without_http_call():
    with patch.object(auth_dep, "_introspect_client", None), patch(
        "src.dependencies.auth.httpx.AsyncClient"
    ) as mock_client:
        with pytest.raises(AuthenticationError) as exc:
            await auth_dep.get_identity(_mk_request("short"))
    assert exc.value.error_code == "INVALID_TOKEN_FORMAT"
    mock_client.assert_not_called()


# ── successful introspect ────────────────────────────────────────────────────


async def test_successful_introspect_returns_identity():
    body = _introspect_body()
    mock_client = MagicMock()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = body
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch.object(auth_dep, "_introspect_client", mock_client):
        identity = await auth_dep.get_identity(_mk_request("dbos_pat_abcdefghij1234567890"))

    assert identity.user_id == "usr_abc"
    assert identity.username == "alice"
    assert identity.actor_type == "user"
    assert identity.department_id == "dep_1"
    assert identity.allowed_services == ["secret_service"]
    assert identity.service_roles == {"secret_service": ["reader"]}


# ── banned / inactive ────────────────────────────────────────────────────────


async def test_banned_user_raises_401():
    body = _introspect_body(is_banned=True)
    mock_client = MagicMock()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = body
    mock_client.post = AsyncMock(return_value=mock_response)
    with patch.object(auth_dep, "_introspect_client", mock_client):
        with pytest.raises(AuthenticationError) as exc:
            await auth_dep.get_identity(_mk_request("dbos_pat_abcdefghij1234567890"))
    assert exc.value.error_code == "UNAUTHORIZED"


async def test_inactive_token_raises_401():
    body = _introspect_body(active=False)
    mock_client = MagicMock()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = body
    mock_client.post = AsyncMock(return_value=mock_response)
    with patch.object(auth_dep, "_introspect_client", mock_client):
        with pytest.raises(AuthenticationError) as exc:
            await auth_dep.get_identity(_mk_request("dbos_pat_abcdefghij1234567890"))
    assert exc.value.error_code == "UNAUTHORIZED"


# ── auth_service unreachable → 503 ───────────────────────────────────────────


async def test_auth_service_timeout_raises_503():
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("boom"))
    with patch.object(auth_dep, "_introspect_client", mock_client):
        with pytest.raises(ServiceUnavailableError) as exc:
            await auth_dep.get_identity(_mk_request("dbos_pat_abcdefghij1234567890"))
    assert exc.value.error_code == "AUTH_SERVICE_UNAVAILABLE"


async def test_auth_service_connect_error_raises_503():
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("nope"))
    with patch.object(auth_dep, "_introspect_client", mock_client):
        with pytest.raises(ServiceUnavailableError) as exc:
            await auth_dep.get_identity(_mk_request("dbos_pat_abcdefghij1234567890"))
    assert exc.value.error_code == "AUTH_SERVICE_UNAVAILABLE"


async def test_auth_service_500_raises_503():
    mock_client = MagicMock()
    mock_response = MagicMock(status_code=500)
    mock_client.post = AsyncMock(return_value=mock_response)
    with patch.object(auth_dep, "_introspect_client", mock_client):
        with pytest.raises(ServiceUnavailableError) as exc:
            await auth_dep.get_identity(_mk_request("dbos_pat_abcdefghij1234567890"))
    assert exc.value.error_code == "AUTH_SERVICE_UNAVAILABLE"


# ── cache hit ────────────────────────────────────────────────────────────────


async def test_cache_hit_skips_second_introspect_call():
    body = _introspect_body()
    mock_client = MagicMock()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = body
    mock_client.post = AsyncMock(return_value=mock_response)

    token = "dbos_pat_abcdefghij1234567890"
    with patch.object(auth_dep, "_introspect_client", mock_client):
        await auth_dep.get_identity(_mk_request(token))
        await auth_dep.get_identity(_mk_request(token))

    assert mock_client.post.await_count == 1


async def test_cache_miss_for_different_token():
    body1 = _introspect_body(sub="usr_one")
    body2 = _introspect_body(sub="usr_two")
    mock_client = MagicMock()
    mock_response_1 = MagicMock(status_code=200)
    mock_response_1.json.return_value = body1
    mock_response_2 = MagicMock(status_code=200)
    mock_response_2.json.return_value = body2
    mock_client.post = AsyncMock(side_effect=[mock_response_1, mock_response_2])

    with patch.object(auth_dep, "_introspect_client", mock_client):
        i1 = await auth_dep.get_identity(_mk_request("dbos_pat_aaaaaaaaaa1111111111"))
        i2 = await auth_dep.get_identity(_mk_request("dbos_pat_bbbbbbbbbb2222222222"))

    assert i1.user_id == "usr_one"
    assert i2.user_id == "usr_two"
    assert mock_client.post.await_count == 2


# ── guard helpers ────────────────────────────────────────────────────────────


def _identity(**kwargs):
    base = {
        "user_id": "usr_x",
        "username": "x",
        "actor_type": "user",
        "department_id": "dep_1",
        "allowed_services": ["secret_service"],
        "service_roles": {"secret_service": ["reader"]},
        "is_banned": False,
        "platform_role": None,
    }
    base.update(kwargs)
    return auth_dep.Identity(**base)


def test_require_user_context_rejects_oauth_client():
    ident = _identity(actor_type="oauth_client")
    with pytest.raises(AuthorizationError) as exc:
        auth_dep.require_user_context(ident)
    assert exc.value.error_code == "SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT"


def test_require_user_context_rejects_dep_without_service_access():
    ident = _identity(allowed_services=["server_service"])
    with pytest.raises(AuthorizationError) as exc:
        auth_dep.require_user_context(ident)
    assert exc.value.error_code == "SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT"


def test_require_user_context_passes_for_user_with_access():
    ident = _identity()
    assert auth_dep.require_user_context(ident) is ident


def test_require_account_admin():
    with pytest.raises(AuthorizationError):
        auth_dep.require_account_admin(_identity())
    assert auth_dep.require_account_admin(_identity(platform_role="account_admin"))


def test_require_service_admin():
    with pytest.raises(AuthorizationError):
        auth_dep.require_service_admin(_identity())
    admin = _identity(service_roles={"secret_service": ["admin"]})
    assert auth_dep.require_service_admin(admin)


def test_require_dept_admin_for():
    ident = _identity(platform_role="department_admin", department_id="dep_1")
    assert auth_dep.require_dept_admin_for(ident, "dep_1")
    with pytest.raises(AuthorizationError):
        auth_dep.require_dept_admin_for(ident, "dep_2")
    # account_admin сильнее dep_admin'а.
    acc = _identity(platform_role="account_admin", department_id=None)
    assert auth_dep.require_dept_admin_for(acc, "dep_999")
