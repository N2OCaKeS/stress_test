"""Tests for the pooled introspect ``AsyncClient``.

`_fetch_identity` ходит в auth_service через module-level
``_introspect_client: httpx.AsyncClient``, который собирается FastAPI
``lifespan``-ом в ``src/main.py``:

* startup → builds a single pooled client with ``base_url=auth_service_url``,
  bounded ``Limits(max_connections=20, max_keepalive_connections=10)``, and
  ``verify=settings.introspect_tls_verify``;
* ``_fetch_identity`` uses the pool when set; иначе (ad-hoc, lifespan не
  стартовал) открывает эфемерный ``AsyncClient`` ровно на один запрос —
  sync httpx нигде не используется;
* shutdown → ``await _introspect_client.aclose()``.

Тесты покрывают:

* lifespan startup/shutdown семантику на реальном ``create_application()``;
* pooled-path использует существующий ``AsyncClient`` под N параллельных
  ``_fetch_identity`` вместо создания новых;
* fallback-path (pool=None) открывает эфемерный ``AsyncClient`` с тем же
  wire-format'ом, что pooled — и с тем же ``verify``-флагом;
* sanity: pooled-path шлёт ``X-Service-Identity`` + ``Authorization``;
* TLS-verify флаг доходит до pooled-клиента.

We avoid the ``pytest.mark.asyncio`` marker (no ``pytest-asyncio`` in deps)
and drive async code via ``asyncio.run`` inside synchronous tests — keeps
parity with the rest of the suite (``test_concurrency.py`` uses the same
pattern when it needs an event loop).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.core.exceptions import AppException
from src.dependencies import auth as auth_dep


# ── helpers ────────────────────────────────────────────────────────────────


def _make_credentials(token: str = "user-jwt-xyz"):
    """Build the credentials object FastAPI passes to ``_fetch_identity``."""
    from fastapi.security import HTTPAuthorizationCredentials
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _fake_request():
    """Minimal stand-in for ``fastapi.Request`` — only ``request.state`` is read."""
    class _State:
        pass
    class _Req:
        state = _State()
    return _Req()


def _fake_settings(
    api_key: str = "pool-test-key",
    *,
    verify: bool = True,
    timeout: float = 3.0,
    connect_timeout: float = 2.0,
    url: str = "http://auth-mock",
    introspect_key: str = "",
):
    return type(
        "FakeSettings",
        (),
        {
            "auth_service_url": url,
            "service_api_key": api_key,
            "introspect_service_api_key": introspect_key,
            "introspect_timeout_seconds": timeout,
            "introspect_connect_timeout_seconds": connect_timeout,
            "introspect_tls_verify": verify,
        },
    )()


# ── lifespan: pool init/teardown ────────────────────────────────────────────


def test_lifespan_startup_initialises_introspect_client(monkeypatch):
    """After lifespan startup, ``_introspect_client`` is a live AsyncClient."""
    auth_dep._introspect_client = None
    monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth-test:8000")
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    from src.core.config import get_settings
    get_settings.cache_clear()

    from src.main import create_application
    app = create_application()

    async def _drive():
        async with app.router.lifespan_context(app):
            assert auth_dep._introspect_client is not None
            assert isinstance(auth_dep._introspect_client, httpx.AsyncClient)
            assert str(auth_dep._introspect_client.base_url).startswith("http://auth-test")
            assert auth_dep._introspect_client.is_closed is False
        # After shutdown — None.
        assert auth_dep._introspect_client is None

    try:
        asyncio.run(_drive())
    finally:
        get_settings.cache_clear()


def test_lifespan_shutdown_closes_introspect_client(monkeypatch):
    """``aclose()`` is actually called on shutdown."""
    auth_dep._introspect_client = None
    monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth-test:8000")
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    from src.core.config import get_settings
    get_settings.cache_clear()

    from src.main import create_application
    app = create_application()
    captured: dict = {}

    async def _drive():
        async with app.router.lifespan_context(app):
            captured["client"] = auth_dep._introspect_client
            assert captured["client"] is not None

    try:
        asyncio.run(_drive())
        assert captured["client"].is_closed is True
    finally:
        get_settings.cache_clear()


def test_lifespan_skips_pool_when_auth_url_unset(monkeypatch):
    """When AUTH_SERVICE_URL is empty/unset (early-dev) the pool stays None
    and ``_fetch_identity`` raises ``AUTH_SERVICE_NOT_CONFIGURED`` if hit
    (без auth_service_url эфемерный fallback тоже не строится — exception
    выбрасывается раньше).
    """
    auth_dep._introspect_client = None
    monkeypatch.delenv("AUTH_SERVICE_URL", raising=False)
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    from src.core.config import get_settings
    get_settings.cache_clear()

    from src.main import create_application
    app = create_application()

    async def _drive():
        async with app.router.lifespan_context(app):
            assert auth_dep._introspect_client is None

    try:
        asyncio.run(_drive())
    finally:
        get_settings.cache_clear()


# ── _fetch_identity pooled path ─────────────────────────────────────────────


def test_pooled_introspect_reuses_single_client(monkeypatch):
    """N concurrent ``_fetch_identity`` calls do NOT create new ``AsyncClient``
    instances — the same pooled client services them all.

    This is the slowloris invariant: one TCP+TLS handshake amortised across
    the lifetime of the process, not N handshakes per N requests.
    """
    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        return httpx.Response(
            200,
            json={
                "active": True,
                "sub": "usr_test",
                "username": "tester",
                "department_id": "dep_a",
                "allowed_services": ["loging_service"],
                "service_roles": {"loging_service": ["reader"]},
                "is_banned": False,
                "platform_role": "loging_admin",
            },
        )

    pooled = httpx.AsyncClient(
        base_url="http://auth-mock",
        transport=httpx.MockTransport(handler),
        timeout=3.0,
    )
    monkeypatch.setattr(auth_dep, "_introspect_client", pooled)
    monkeypatch.setattr(auth_dep, "get_settings", lambda: _fake_settings())

    # Count new AsyncClient creations — should be ZERO with the pool set.
    new_client_count = {"n": 0}
    real_async_client = httpx.AsyncClient

    def counting_factory(*args, **kwargs):
        new_client_count["n"] += 1
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", counting_factory)

    async def _drive():
        creds = _make_credentials("eyJpooled_user_token_long_enough_xx")
        results = await asyncio.gather(*(
            auth_dep._fetch_identity(creds, _fake_request())
            for _ in range(30)
        ))
        return results

    try:
        results = asyncio.run(_drive())
        assert len(results) == 30
        assert all(r["user_id"] == "usr_test" for r in results)
        # auth_service was hit 30 times (1 introspect per call) — but...
        assert request_count["n"] == 30
        # ...NO new httpx.AsyncClient was ever created.
        assert new_client_count["n"] == 0, (
            f"Expected pooled reuse, got {new_client_count['n']} new "
            "AsyncClient — regression to per-call path."
        )
    finally:
        asyncio.run(pooled.aclose())


def test_pooled_introspect_sends_service_api_key_and_identity_header(monkeypatch):
    """Sanity: pooled-path still sends ``Authorization: Bearer <SERVICE_API_KEY>``
    and ``X-Service-Identity: loging_service``. User token goes into the JSON
    body, not into the header. Regression for the header invariant
    (``test_admin_auth.py::test_introspect_call_sends_service_api_key_header``).
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["x_service_identity"] = request.headers.get("X-Service-Identity")
        import json as _json
        captured["body"] = _json.loads(request.content.decode())
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "active": True,
                "sub": "usr_a",
                "username": "a",
                "department_id": "dep_a",
                "platform_role": "loging_admin",
            },
        )

    pooled = httpx.AsyncClient(
        base_url="http://auth-mock",
        transport=httpx.MockTransport(handler),
        timeout=3.0,
    )
    monkeypatch.setattr(auth_dep, "_introspect_client", pooled)
    monkeypatch.setattr(
        auth_dep,
        "get_settings",
        lambda: _fake_settings(api_key="pooled-sa-key"),
    )

    async def _drive():
        creds = _make_credentials("eyJlong_enough_user_jwt_token_xxx")
        return await auth_dep._fetch_identity(creds, _fake_request())

    try:
        result = asyncio.run(_drive())
        assert result["user_id"] == "usr_a"
        assert captured["authorization"] == "Bearer pooled-sa-key"
        assert captured["x_service_identity"] == "loging_service"
        assert captured["body"] == {"token": "eyJlong_enough_user_jwt_token_xxx"}
        # Pooled-path URL is base_url + relative path.
        assert captured["url"].endswith("/api/auth/v1/authorization/introspect")
    finally:
        asyncio.run(pooled.aclose())


def test_pooled_introspect_raises_503_on_timeout(monkeypatch):
    """httpx.TimeoutException from the pooled client surfaces as 503
    AUTH_SERVICE_TIMEOUT — same envelope as the fallback path.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout")

    pooled = httpx.AsyncClient(
        base_url="http://auth-mock",
        transport=httpx.MockTransport(handler),
        timeout=0.1,
    )
    monkeypatch.setattr(auth_dep, "_introspect_client", pooled)
    monkeypatch.setattr(auth_dep, "get_settings", lambda: _fake_settings())

    async def _drive():
        await auth_dep._fetch_identity(
            _make_credentials("eyJtimeout_token_long_enough_xx"),
            _fake_request(),
        )

    try:
        with pytest.raises(AppException) as excinfo:
            asyncio.run(_drive())
        assert excinfo.value.http_status == 503
        assert excinfo.value.error_code == "AUTH_SERVICE_TIMEOUT"
    finally:
        asyncio.run(pooled.aclose())


def test_pooled_introspect_raises_503_on_connect_error(monkeypatch):
    """``httpx.ConnectError`` → 503 ``AUTH_SERVICE_UNREACHABLE``."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    pooled = httpx.AsyncClient(
        base_url="http://auth-mock",
        transport=httpx.MockTransport(handler),
        timeout=3.0,
    )
    monkeypatch.setattr(auth_dep, "_introspect_client", pooled)
    monkeypatch.setattr(auth_dep, "get_settings", lambda: _fake_settings())

    async def _drive():
        await auth_dep._fetch_identity(
            _make_credentials("eyJconnect_err_token_long_enough_x"),
            _fake_request(),
        )

    try:
        with pytest.raises(AppException) as excinfo:
            asyncio.run(_drive())
        assert excinfo.value.http_status == 503
        assert excinfo.value.error_code == "AUTH_SERVICE_UNREACHABLE"
    finally:
        asyncio.run(pooled.aclose())


def test_pooled_introspect_active_false_raises_invalid_token(monkeypatch):
    """``active=false`` from auth_service → 401 ``INVALID_TOKEN`` (regression)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"active": False, "sub": ""})

    pooled = httpx.AsyncClient(
        base_url="http://auth-mock",
        transport=httpx.MockTransport(handler),
        timeout=3.0,
    )
    monkeypatch.setattr(auth_dep, "_introspect_client", pooled)
    monkeypatch.setattr(auth_dep, "get_settings", lambda: _fake_settings())

    async def _drive():
        await auth_dep._fetch_identity(
            _make_credentials("eyJinactive_token_long_enough_xx"),
            _fake_request(),
        )

    try:
        with pytest.raises(AppException) as excinfo:
            asyncio.run(_drive())
        assert excinfo.value.http_status == 401
        assert excinfo.value.error_code == "INVALID_TOKEN"
    finally:
        asyncio.run(pooled.aclose())


# ── _fetch_identity fallback path (backward compat) ─────────────────────────


def test_fallback_path_uses_ephemeral_async_client_when_pool_none(monkeypatch):
    """When ``_introspect_client is None`` (ad-hoc, lifespan не запускался),
    `_fetch_identity` открывает короткоживущий `AsyncClient` ровно на один
    запрос — sync `httpx.post` нигде не используется.

    Регрессия: pool=None НЕ должен молча проваливаться или блокировать
    event-loop sync-вызовом.
    """
    monkeypatch.setattr(auth_dep, "_introspect_client", None)
    monkeypatch.setattr(auth_dep, "get_settings", lambda: _fake_settings())

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["x_service_identity"] = request.headers.get("X-Service-Identity")
        import json as _json
        captured["body"] = _json.loads(request.content.decode())
        return httpx.Response(200, json={
            "active": True, "sub": "usr_fallback",
            "username": "fb", "platform_role": "loging_admin",
        })

    real_async_client = httpx.AsyncClient
    ephemeral_count = {"n": 0}

    def factory(*args, **kwargs):
        ephemeral_count["n"] += 1
        # Подсовываем MockTransport, чтобы запрос не ушёл в реальную сеть.
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", factory)

    async def _drive():
        return await auth_dep._fetch_identity(
            _make_credentials("eyJfallback_token_long_enough_xxx"),
            _fake_request(),
        )

    result = asyncio.run(_drive())
    assert result["user_id"] == "usr_fallback"
    # Ровно один эфемерный AsyncClient на запрос.
    assert ephemeral_count["n"] == 1
    # Wire-format совпадает с pooled-путём.
    assert captured["body"] == {"token": "eyJfallback_token_long_enough_xxx"}
    assert captured["authorization"] == "Bearer pool-test-key"
    assert captured["x_service_identity"] == "loging_service"
    assert captured["url"].endswith("/api/auth/v1/authorization/introspect")


def test_fallback_path_honours_introspect_tls_verify(monkeypatch):
    """``introspect_tls_verify`` пробрасывается в эфемерный `AsyncClient`
    (как kwarg `verify=` при создании). Default True — production. False —
    devcontainer/local с self-signed.
    """
    monkeypatch.setattr(auth_dep, "_introspect_client", None)
    monkeypatch.setattr(
        auth_dep,
        "get_settings",
        lambda: _fake_settings(api_key="tls-verify-key", verify=False),
    )

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "active": True, "sub": "usr_v",
            "username": "v", "platform_role": "loging_admin",
        })

    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        captured["verify"] = kwargs.get("verify")
        captured["timeout"] = kwargs.get("timeout")
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(auth_dep.httpx, "AsyncClient", factory)

    async def _drive():
        return await auth_dep._fetch_identity(
            _make_credentials("eyJverify_test_token_long_enough_x"),
            _fake_request(),
        )

    asyncio.run(_drive())
    assert captured["verify"] is False
    # Timeout пробрасывается как httpx.Timeout(read=..., connect=...).
    assert isinstance(captured["timeout"], httpx.Timeout)
    assert captured["timeout"].read == 3.0
    assert captured["timeout"].connect == 2.0


# ── TLS verify flag is propagated into the pooled client ────────────────────


def test_pool_built_with_introspect_tls_verify_default_true(monkeypatch):
    """Default ``INTROSPECT_TLS_VERIFY=True`` propagates into the pooled
    ``httpx.AsyncClient`` ``verify`` parameter. Captured by snooping on
    ``httpx.AsyncClient.__init__`` kwargs during lifespan startup.
    """
    auth_dep._introspect_client = None
    monkeypatch.setenv("AUTH_SERVICE_URL", "https://auth-test:8443")
    monkeypatch.setenv("SERVICE_API_KEY", "tls-key")
    monkeypatch.delenv("INTROSPECT_TLS_VERIFY", raising=False)
    from src.core.config import get_settings
    get_settings.cache_clear()

    captured_kwargs: dict = {}
    real_async_client = httpx.AsyncClient

    def snoop(*args, **kwargs):
        # Lifespan теперь поднимает два AsyncClient: introspect (max=20) и
        # /token-proxy (max=10). Нас интересует первый (introspect) — не
        # перезаписываем, если уже захватили.
        if "verify" in kwargs and "base_url" in kwargs and not captured_kwargs:
            captured_kwargs.update(kwargs)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("src.main.httpx.AsyncClient", snoop)

    from src.main import create_application
    app = create_application()

    async def _drive():
        async with app.router.lifespan_context(app):
            pass

    try:
        asyncio.run(_drive())
    finally:
        get_settings.cache_clear()

    assert captured_kwargs.get("verify") is True
    assert "limits" in captured_kwargs
    limits = captured_kwargs["limits"]
    assert limits.max_connections == 20
    assert limits.max_keepalive_connections == 10


def test_pool_built_with_introspect_tls_verify_false_when_env_set(monkeypatch):
    """``INTROSPECT_TLS_VERIFY=false`` is honoured in lifespan startup
    (devcontainer / self-signed-cert scenario).
    """
    auth_dep._introspect_client = None
    monkeypatch.setenv("AUTH_SERVICE_URL", "https://auth-test:8443")
    monkeypatch.setenv("SERVICE_API_KEY", "tls-key")
    monkeypatch.setenv("INTROSPECT_TLS_VERIFY", "false")
    from src.core.config import get_settings
    get_settings.cache_clear()

    captured_kwargs: dict = {}
    real_async_client = httpx.AsyncClient

    def snoop(*args, **kwargs):
        if "verify" in kwargs and "base_url" in kwargs:
            captured_kwargs.update(kwargs)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("src.main.httpx.AsyncClient", snoop)

    from src.main import create_application
    app = create_application()

    async def _drive():
        async with app.router.lifespan_context(app):
            pass

    try:
        asyncio.run(_drive())
    finally:
        get_settings.cache_clear()

    assert captured_kwargs.get("verify") is False
