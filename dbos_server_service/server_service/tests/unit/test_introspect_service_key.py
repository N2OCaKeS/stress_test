"""Unit-тест: `_introspect` шлёт `Authorization: Bearer <SERVICE_API_KEY>`.

Парный к auth_service-фиксу для introspect-авторизации.
В conftest.py фикстура `_patch_introspect` подменяет `_introspect` целиком,
поэтому реальный httpx-вызов в integration-тестах не уходит. Этот файл проверяет
raw-функцию без monkeypatch — что header service-key действительно ставится.

Подход: подменяем `httpx.AsyncClient` на класс с `MockTransport`, перехватываем
запрос и проверяем headers + body.
"""

from __future__ import annotations

import httpx
import pytest

from src.dependencies import auth as auth_dep

# Сохраняем оригинальный `_introspect` ДО того, как autouse-фикстура
# `_patch_introspect` в conftest.py его подменит. Нам нужна именно реальная
# функция с httpx-вызовом.
_REAL_INTROSPECT = auth_dep._introspect


@pytest.mark.asyncio
async def test_introspect_sends_service_api_key_header(monkeypatch):
    """`_introspect` шлёт корректный Bearer header с SERVICE_API_KEY и user token в body."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["authorization"] = request.headers.get("Authorization")
        # body — JSON с user token
        import json as _json
        captured["body"] = _json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "active": True,
                "sub": "usr_test",
                "username": "tester",
                "department_id": "dep_a",
                "allowed_services": ["server_service"],
                "service_roles": {"server_service": ["reader"]},
                "is_banned": False,
                "platform_role": None,
            },
        )

    transport = httpx.MockTransport(handler)

    # Подменяем настройки в lru_cache: задаём известный URL и ключ
    fake_settings = type(
        "FakeSettings",
        (),
        {
            "auth_service_url": "http://auth-mock",
            "auth_request_timeout_seconds": 3.0,
            "service_api_key": "test-service-key-shared",
        },
    )()
    monkeypatch.setattr(auth_dep, "get_settings", lambda: fake_settings)

    # Перехватываем создание AsyncClient — отдаём клиент с MockTransport
    real_async_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", _client_factory)

    # Shape-valid PAT — иначе bearer-shape pre-check отсечёт до httpx.
    result = await _REAL_INTROSPECT("dbos_pat_user_token_xyz12345")

    assert result["active"] is True
    assert captured["method"] == "POST"
    assert captured["url"] == "http://auth-mock/api/auth/v1/authorization/introspect"
    assert captured["authorization"] == "Bearer test-service-key-shared"
    # user token идёт в body, не в Authorization
    assert captured["body"] == {"token": "dbos_pat_user_token_xyz12345"}


@pytest.mark.asyncio
async def test_introspect_sends_header_even_when_service_key_empty(monkeypatch):
    """Если SERVICE_API_KEY пуст (dev-default) — header всё равно ставится как `Bearer `.

    Это намеренно: auth_service вернёт 401, и мы поднимем 503 — пусть будет
    очевидная сетевая ошибка вместо тихого пропуска header'а.
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(401, json={"error_code": "INVALID_SERVICE_TOKEN"})

    transport = httpx.MockTransport(handler)
    fake_settings = type(
        "FakeSettings",
        (),
        {
            "auth_service_url": "http://auth-mock",
            "auth_request_timeout_seconds": 3.0,
            "service_api_key": "",
        },
    )()
    monkeypatch.setattr(auth_dep, "get_settings", lambda: fake_settings)

    real_async_client = httpx.AsyncClient

    def _client_factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", _client_factory)

    from src.core.exceptions import ServiceUnavailableError

    with pytest.raises(ServiceUnavailableError):
        # Shape-valid токен, чтобы тест дошёл до httpx-вызова, а не упал на
        # bearer-shape pre-check.
        await _REAL_INTROSPECT("dbos_pat_any_token_xyz12345")

    assert captured["authorization"] == "Bearer "
