"""Unit-тесты: module-level `_introspect_client` инициализируется в lifespan
и переиспользует pooled `httpx.AsyncClient` (slowloris-фикс).

Парный к фиксу dependencies/auth.py:51 — раньше `_introspect()` создавал
`httpx.AsyncClient` per-request, что под нагрузкой убивало FD-пул и
открывало slowloris-вектор через 41 «защищённый» stub-эндпоинт. Теперь:

* startup lifespan → `auth_deps._introspect_client = httpx.AsyncClient(...)`
* `_introspect()` использует module-level client (re-используется TCP-pool)
* shutdown lifespan → `await client.aclose()`

Тесты в этом файле НЕ используют `_patch_introspect`-фикстуру из conftest
(она monkeypatch'ит `_introspect` целиком). Поэтому проверяем raw-функцию
по аналогии с `test_introspect_service_key.py`.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import AuthenticationError
from src.dependencies import auth as auth_dep
from src.main import create_application

# Сохраняем оригинальный `_introspect` ДО того, как autouse-фикстура
# `_patch_introspect` в conftest.py его подменит.
_REAL_INTROSPECT = auth_dep._introspect


@pytest.mark.asyncio
async def test_lifespan_startup_initialises_introspect_client(monkeypatch):
    """После старта lifespan `auth_deps._introspect_client` — живой `AsyncClient`."""
    # На входе пуст
    auth_dep._introspect_client = None

    # Не запускаем `_run_startup_audit_sequence` — он делает реальный httpx
    # вызов в loging_service (register_events) + audit emit. Заглушаем
    # async-noop, чтобы не сетка по live loging_service.
    async def _noop() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop)

    app = create_application()
    async with app.router.lifespan_context(app):
        assert auth_dep._introspect_client is not None
        assert isinstance(auth_dep._introspect_client, httpx.AsyncClient)
        # `base_url` должен быть выставлен из settings
        assert str(auth_dep._introspect_client.base_url).startswith("http")
        # client живой, не закрыт
        assert auth_dep._introspect_client.is_closed is False

    # После shutdown — обнулён и закрыт
    assert auth_dep._introspect_client is None


@pytest.mark.asyncio
async def test_lifespan_shutdown_closes_introspect_client(monkeypatch):
    """`aclose()` действительно вызывается на shutdown."""
    auth_dep._introspect_client = None
    async def _noop() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop)

    captured: dict = {}

    app = create_application()
    async with app.router.lifespan_context(app):
        client = auth_dep._introspect_client
        assert client is not None
        captured["client"] = client

    # Из контекста вышли → клиент должен быть закрыт.
    assert captured["client"].is_closed is True


@pytest.mark.asyncio
async def test_introspect_reuses_pooled_client(monkeypatch):
    """Последовательные вызовы `_introspect` НЕ создают новый httpx.AsyncClient.

    Это и есть anti-slowloris инвариант: одна сессия — много введений.
    Подсчёт создаваемых клиентов через mock counter.
    """
    # Готовим pooled client с MockTransport (имитирует auth_service).
    call_count = {"requests": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["requests"] += 1
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

    pooled_client = httpx.AsyncClient(
        base_url="http://auth-mock",
        transport=httpx.MockTransport(handler),
        timeout=3.0,
    )

    # Settings — для service_api_key. base_url нам не нужен (он уже в клиенте).
    fake_settings = type(
        "FakeSettings",
        (),
        {
            "auth_service_url": "http://auth-mock",
            "auth_request_timeout_seconds": 3.0,
            "service_api_key": "test-service-key",
        },
    )()
    monkeypatch.setattr(auth_dep, "get_settings", lambda: fake_settings)

    # Ставим module-level client (имитируем после-lifespan состояние)
    monkeypatch.setattr(auth_dep, "_introspect_client", pooled_client)

    # Считаем, сколько раз создаётся новый AsyncClient в процессе вызовов.
    # Если фикс работает — НИ ОДНОГО (используется pooled).
    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", counting_factory)

    try:
        # Несколько последовательных вызовов. Используем shape-valid PAT,
        # иначе bearer-shape pre-check (slowloris (c)) отсечёт до HTTP.
        for _ in range(5):
            result = await _REAL_INTROSPECT("dbos_pat_user_token_xyz12345")
            assert result["active"] is True

        # MockTransport принял 5 запросов
        assert call_count["requests"] == 5
        # Но НИ ОДНОГО нового AsyncClient не создано — использовался pooled
        assert new_client_counter["created"] == 0
    finally:
        await pooled_client.aclose()


@pytest.mark.asyncio
async def test_introspect_falls_back_to_per_call_when_pool_uninitialised(monkeypatch):
    """Когда `_introspect_client is None` (вне lifespan) — fallback создаёт per-call.

    Это поведение нужно для unit-тестов, импортирующих модуль до старта app
    (как `test_introspect_service_key.py`). Production-путь всегда идёт через пул.
    """
    monkeypatch.setattr(auth_dep, "_introspect_client", None)

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "auth_service_url": "http://auth-mock",
            "auth_request_timeout_seconds": 3.0,
            "service_api_key": "test-service-key",
        },
    )()
    monkeypatch.setattr(auth_dep, "get_settings", lambda: fake_settings)

    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "active": True,
                "sub": "usr_test",
                "username": "tester",
                "department_id": "dep_a",
                "allowed_services": ["server_service"],
                "service_roles": {},
                "is_banned": False,
                "platform_role": None,
            },
        )

    transport = httpx.MockTransport(handler)

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        # Пропускаем переданные kwargs, подмешиваем MockTransport
        kwargs.pop("transport", None)
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", counting_factory)

    # Вызываем дважды — должно быть создано 2 клиента (по одному на вызов).
    # Токены shape-valid, иначе bearer-shape pre-check отрежет до httpx.
    await _REAL_INTROSPECT("dbos_pat_tok_one_xyz123456")
    await _REAL_INTROSPECT("dbos_pat_tok_two_xyz123456")

    assert new_client_counter["created"] == 2


@pytest.mark.asyncio
async def test_introspect_pooled_sends_service_api_key_header(monkeypatch):
    """Sanity: pooled-путь по-прежнему ставит `Authorization: Bearer <key>`
    и пробрасывает user-token в body (регрессия для `test_introspect_service_key`)."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
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
                "service_roles": {},
                "is_banned": False,
                "platform_role": None,
            },
        )

    pooled_client = httpx.AsyncClient(
        base_url="http://auth-mock",
        transport=httpx.MockTransport(handler),
        timeout=3.0,
    )

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "auth_service_url": "http://auth-mock",
            "auth_request_timeout_seconds": 3.0,
            "service_api_key": "pooled-service-key",
        },
    )()
    monkeypatch.setattr(auth_dep, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(auth_dep, "_introspect_client", pooled_client)

    try:
        # `user-tok` слишком короткий и без префикса — добавляем валидный
        # PAT-токен, чтобы пройти bearer-shape pre-check (slowloris (c)).
        result = await _REAL_INTROSPECT("dbos_pat_pooled_user_tok_xyz")
        assert result["active"] is True
        assert captured["authorization"] == "Bearer pooled-service-key"
        assert captured["body"] == {"token": "dbos_pat_pooled_user_tok_xyz"}
        # На pooled-пути url относительный (base_url + path)
        assert captured["url"].endswith("/api/auth/v1/authorization/introspect")
    finally:
        await pooled_client.aclose()


# --- Slowloris-mitigation (c): bearer-shape pre-check -----------------------
#
# Любой токен, не похожий формой на реальный bearer (`eyJ…` / `dbos_pat_…` /
# `dbos_bot_…`, длина >= 20), должен отсекаться 401-кой ДО HTTP-вызова в
# auth_service. Парный к фиксу `dependencies/auth.py:_is_token_shape_valid`.


def _make_counting_pool(monkeypatch, response_json: dict | None = None) -> dict:
    """Подсовываем pooled-client с MockTransport и счётчиком запросов.

    Возвращает dict с ключами:
      * `requests` — сколько HTTP-вызовов реально дошло до auth_service-mock;
      * `client`   — pooled `httpx.AsyncClient` (закрывает caller через
                     `addfinalizer`, но в текущем виде утечка не критична —
                     monkeypatch авто-откатит `_introspect_client`).
    """
    counter: dict = {"requests": 0}

    body = response_json or {
        "active": False,
        "sub": "",
        "username": "",
        "department_id": None,
        "allowed_services": [],
        "service_roles": {},
        "is_banned": False,
        "platform_role": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        counter["requests"] += 1
        return httpx.Response(200, json=body)

    pooled_client = httpx.AsyncClient(
        base_url="http://auth-mock",
        transport=httpx.MockTransport(handler),
        timeout=3.0,
    )

    fake_settings = type(
        "FakeSettings",
        (),
        {
            "auth_service_url": "http://auth-mock",
            "auth_request_timeout_seconds": 3.0,
            "service_api_key": "shape-precheck-key",
        },
    )()
    monkeypatch.setattr(auth_dep, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(auth_dep, "_introspect_client", pooled_client)

    counter["client"] = pooled_client
    return counter


@pytest.mark.asyncio
async def test_introspect_rejects_short_garbage_token_without_roundtrip(monkeypatch):
    """`Bearer abc` → 401 `INVALID_TOKEN_FORMAT`, HTTP-вызов НЕ происходит."""
    counter = _make_counting_pool(monkeypatch)
    try:
        with pytest.raises(AuthenticationError) as excinfo:
            await _REAL_INTROSPECT("abc")
        assert excinfo.value.error_code == "INVALID_TOKEN_FORMAT"
        assert excinfo.value.http_status == 401
        # Ключевой инвариант: auth_service НЕ дёрнут.
        assert counter["requests"] == 0
    finally:
        await counter["client"].aclose()


@pytest.mark.asyncio
async def test_introspect_rejects_long_garbage_without_prefix_no_roundtrip(monkeypatch):
    """Длинный токен без валидного префикса (>=20 символов) — всё равно отсекается."""
    counter = _make_counting_pool(monkeypatch)
    try:
        long_garbage = "a" * 64  # длина ок, но префикс не из whitelist
        with pytest.raises(AuthenticationError) as excinfo:
            await _REAL_INTROSPECT(long_garbage)
        assert excinfo.value.error_code == "INVALID_TOKEN_FORMAT"
        assert counter["requests"] == 0
    finally:
        await counter["client"].aclose()


@pytest.mark.asyncio
async def test_introspect_rejects_short_jwt_prefix_no_roundtrip(monkeypatch):
    """`eyJ…` короче 20 символов — всё равно отсекается (длина < min)."""
    counter = _make_counting_pool(monkeypatch)
    try:
        with pytest.raises(AuthenticationError) as excinfo:
            await _REAL_INTROSPECT("eyJ_short")  # начинается с eyJ, но len < 20
        assert excinfo.value.error_code == "INVALID_TOKEN_FORMAT"
        assert counter["requests"] == 0
    finally:
        await counter["client"].aclose()


@pytest.mark.asyncio
async def test_introspect_accepts_jwt_shaped_token_and_roundtrips(monkeypatch):
    """`Bearer eyJ_garbage…` → доходит до auth_service, тот отвечает active=False."""
    counter = _make_counting_pool(monkeypatch, response_json={
        "active": False,
        "sub": "",
        "username": "",
        "department_id": None,
        "allowed_services": [],
        "service_roles": {},
        "is_banned": False,
        "platform_role": None,
    })
    try:
        # >=20 символов, префикс eyJ — shape ок, должен дойти до auth_service.
        result = await _REAL_INTROSPECT("eyJ_garbage_but_long_enough_xxx")
        # auth_service вернул active=False — это нормальный ответ, не raise.
        assert result["active"] is False
        assert counter["requests"] == 1
    finally:
        await counter["client"].aclose()


@pytest.mark.asyncio
async def test_introspect_accepts_pat_shaped_token_and_roundtrips(monkeypatch):
    """`Bearer dbos_pat_…` — корректная форма PAT, roundtrip должен случиться."""
    counter = _make_counting_pool(monkeypatch, response_json={
        "active": True,
        "sub": "usr_test",
        "username": "tester",
        "department_id": "dep_a",
        "allowed_services": ["server_service"],
        "service_roles": {"server_service": ["reader"]},
        "is_banned": False,
        "platform_role": None,
    })
    try:
        result = await _REAL_INTROSPECT("dbos_pat_validlooking_token_xyz")
        assert result["active"] is True
        assert counter["requests"] == 1
    finally:
        await counter["client"].aclose()


@pytest.mark.asyncio
async def test_introspect_accepts_bot_shaped_token_and_roundtrips(monkeypatch):
    """`Bearer dbos_bot_…` — корректная форма bot-токена, roundtrip случается."""
    counter = _make_counting_pool(monkeypatch, response_json={
        "active": True,
        "sub": "bot_test",
        "username": "test-bot",
        "department_id": "dep_a",
        "allowed_services": ["server_service"],
        "service_roles": {"server_service": ["operator"]},
        "is_banned": False,
        "platform_role": None,
    })
    try:
        result = await _REAL_INTROSPECT("dbos_bot_validlooking_token_xyz")
        assert result["active"] is True
        assert counter["requests"] == 1
    finally:
        await counter["client"].aclose()


def test_is_token_shape_valid_unit():
    """Прямой unit на helper — без асинхронщины и моков."""
    valid = [
        "eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "dbos_pat_abcdefghij12345",
        "dbos_bot_abcdefghij12345",
    ]
    for tok in valid:
        assert auth_dep._is_token_shape_valid(tok) is True, tok

    invalid = [
        "",                           # пусто
        "abc",                        # короткий, без префикса
        "12345",                      # короткий, без префикса
        "a" * 100,                    # длинный, без префикса
        "eyJ_short",                  # eyJ-префикс, но < 20
        "dbos_pat_",                  # префикс ок, но < 20
        "dbos_bot_",                  # префикс ок, но < 20
        "Bearer eyJaaaaaaaaaaaaaaaa",  # содержит схему `Bearer ` — не префикс токена
    ]
    for tok in invalid:
        assert auth_dep._is_token_shape_valid(tok) is False, tok
