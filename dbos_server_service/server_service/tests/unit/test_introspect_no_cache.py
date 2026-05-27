"""Unit-тесты: introspect свежий на каждом запросе, без кэша.

Раньше результат introspect кэшировался TTL-кэшем на 5 секунд — отозванный
или забаненный токен продолжал работать до истечения окна. Кэш убран:
каждый запрос идёт свежим `_introspect(token)`, отзыв действует мгновенно.

Эти тесты проверяют:

* `get_current_identity` зовёт `_introspect` ровно один раз на вызов
  (N вызовов → N introspect'ов, без переиспользования);
* токен, который auth_service начал отдавать как `active=False`
  (revoke/ban), отбивается на следующем же запросе, без задержки;
* `platform_admin_guard` middleware тоже идёт свежим introspect'ом.
"""

from __future__ import annotations

import pytest
from fastapi import Request

from src.core.exceptions import AuthenticationError
from src.dependencies import auth as auth_dep
from src.middleware import platform_admin_guard as guard_mod


def _active_body(sub: str = "usr_1") -> dict:
    return {
        "active": True,
        "sub": sub,
        "username": "alice",
        "department_id": "dep_a",
        "platform_role": None,
        "service_roles": {"server_service": ["reader"]},
        "allowed_services": ["server_service"],
        "is_banned": False,
    }


def _request_with_token(token: str) -> Request:
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer " + token.encode())],
        "client": ("127.0.0.1", 0),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_get_current_identity_introspects_every_request(monkeypatch):
    """N запросов одним токеном → N introspect'ов (кэша нет)."""
    state = {"calls": 0}

    async def spy_introspect(token: str) -> dict:
        state["calls"] += 1
        return _active_body()

    monkeypatch.setattr(auth_dep, "_introspect", spy_introspect)

    token = "dbos_pat_same_token_xyz_0001"
    request = _request_with_token(token)
    for _ in range(5):
        identity = await auth_dep.get_current_identity(request)
        assert identity.user_id == "usr_1"

    assert state["calls"] == 5


@pytest.mark.asyncio
async def test_revoked_token_rejected_immediately(monkeypatch):
    """Токен, ставший `active=False` после revoke, отбивается на следующем запросе."""
    state = {"calls": 0}

    async def spy_introspect(token: str) -> dict:
        state["calls"] += 1
        # Первый запрос валиден, после revoke auth_service отдаёт active=False.
        if state["calls"] == 1:
            return _active_body()
        return {"active": False}

    monkeypatch.setattr(auth_dep, "_introspect", spy_introspect)

    token = "dbos_pat_revoke_test_xyz_002"
    request = _request_with_token(token)

    identity = await auth_dep.get_current_identity(request)
    assert identity.user_id == "usr_1"

    with pytest.raises(AuthenticationError) as exc:
        await auth_dep.get_current_identity(request)
    assert exc.value.error_code == "ACCESS_TOKEN_INVALID"
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_platform_admin_guard_introspects_every_request(monkeypatch):
    """Middleware идёт свежим introspect'ом на каждом запросе."""
    state = {"calls": 0}

    async def spy_introspect(token: str) -> dict:
        state["calls"] += 1
        return _active_body()

    monkeypatch.setattr(auth_dep, "_introspect", spy_introspect)

    async def call_next(_request):
        return "passed"

    token = "dbos_pat_guard_token_xyz_0003"
    for _ in range(3):
        request = _request_with_token(token)
        request.scope["path"] = "/api/server/v1/servers"
        result = await guard_mod.platform_admin_guard(request, call_next)
        assert result == "passed"

    assert state["calls"] == 3
