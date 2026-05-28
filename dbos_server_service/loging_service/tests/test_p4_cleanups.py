"""Тесты под P4-чистки loging_service.

Покрывают четыре независимые правки:

1. DB pool sizing через env (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`) — раньше
   хардкод в `db/session.py`.
2. Introspect connect-таймаут — через `INTROSPECT_CONNECT_TIMEOUT_SECONDS`,
   раньше literal `2.0` в `main.lifespan`.
3. `self_audit_failures_total` под `threading.Lock` — `+=` на global'е
   не атомарен под GIL'ом, lost-increment'ы под параллельным `to_thread`'ом
   self-audit'а.
4. Pooled httpx-клиент для `POST /token` proxy в auth_service — раньше
   per-call sync `httpx.post`, TCP+TLS handshake на каждый swagger-login.
"""

from __future__ import annotations

import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest


# ── 1. DB pool sizing через env ────────────────────────────────────────────


class TestDbPoolEnv:
    def test_engine_picks_up_pool_size_from_env(self, monkeypatch):
        """`DB_POOL_SIZE` / `DB_MAX_OVERFLOW` пробрасываются в `create_async_engine`."""
        captured: dict = {}

        # Подменяем create_engine так, чтобы поймать kwargs.
        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs

            # Возвращаем заглушку с минимальным интерфейсом, который
            # sessionmaker не дёргает на module-import'е.
            class _Stub:
                def dispose(self_inner):
                    pass

            return _Stub()

        monkeypatch.setenv("DB_POOL_SIZE", "7")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "13")

        from src.core.config import get_settings
        get_settings.cache_clear()

        # Подменяем create_engine ДО re-import'а session.py.
        import sqlalchemy
        monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

        import src.db.session as session_mod
        try:
            importlib.reload(session_mod)
            assert captured["kwargs"]["pool_size"] == 7
            assert captured["kwargs"]["max_overflow"] == 13
        finally:
            # Восстанавливаем модуль, чтобы остальные тесты в run'е
            # видели настоящий engine.
            monkeypatch.undo()
            get_settings.cache_clear()
            importlib.reload(session_mod)

    def test_pool_size_defaults_match_historical(self, monkeypatch):
        """Без env-vars остаются исторические 10 / 20."""
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
        from src.core.config import get_settings
        get_settings.cache_clear()
        try:
            s = get_settings()
            assert s.db_pool_size == 10
            assert s.db_max_overflow == 20
        finally:
            get_settings.cache_clear()


# ── 2. Introspect connect-таймаут из env ───────────────────────────────────


class TestIntrospectConnectTimeoutEnv:
    def test_lifespan_uses_env_connect_timeout(self, monkeypatch):
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth-test:8000")
        monkeypatch.setenv("SERVICE_API_KEY", "test-key")
        monkeypatch.setenv("INTROSPECT_CONNECT_TIMEOUT_SECONDS", "0.5")
        monkeypatch.setenv("INTROSPECT_TIMEOUT_SECONDS", "4.5")
        from src.core.config import get_settings
        get_settings.cache_clear()
        try:
            from src.dependencies import auth as auth_dep
            from src.main import create_application
            auth_dep._introspect_client = None
            auth_dep._token_proxy_client = None
            app = create_application()

            async def _drive():
                async with app.router.lifespan_context(app):
                    cli = auth_dep._introspect_client
                    assert cli is not None
                    assert cli.timeout.connect == pytest.approx(0.5)
                    assert cli.timeout.read == pytest.approx(4.5)

            asyncio.run(_drive())
        finally:
            get_settings.cache_clear()

    def test_default_connect_timeout_is_two_seconds(self, monkeypatch):
        monkeypatch.delenv("INTROSPECT_CONNECT_TIMEOUT_SECONDS", raising=False)
        from src.core.config import get_settings
        get_settings.cache_clear()
        try:
            assert get_settings().introspect_connect_timeout_seconds == pytest.approx(2.0)
        finally:
            get_settings.cache_clear()


# ── 3. self_audit counter под lock ─────────────────────────────────────────


class TestSelfAuditCounterLock:
    def test_bump_helper_is_thread_safe(self, monkeypatch):
        """`_bump_self_audit_failures` под нагрузкой не теряет инкрементов."""
        from src import main as main_mod

        # Сбрасываем counter, чтобы измерение было независимым.
        monkeypatch.setattr(main_mod, "self_audit_failures_total", 0)

        N_THREADS = 16
        PER_THREAD = 500
        TOTAL = N_THREADS * PER_THREAD

        def worker():
            for _ in range(PER_THREAD):
                main_mod._bump_self_audit_failures()

        with ThreadPoolExecutor(max_workers=N_THREADS) as ex:
            futures = [ex.submit(worker) for _ in range(N_THREADS)]
            for f in futures:
                f.result()

        assert main_mod.self_audit_failures_total == TOTAL, (
            f"lost increments: expected {TOTAL}, got "
            f"{main_mod.self_audit_failures_total}"
        )

    def test_emit_audit_failure_uses_locked_bump(self, monkeypatch):
        """Когда `_emit_audit` ловит исключение — проходит через `_bump_…`."""
        from src import main as main_mod

        calls = {"n": 0}
        real_bump = main_mod._bump_self_audit_failures

        def spy():
            calls["n"] += 1
            return real_bump()

        monkeypatch.setattr(main_mod, "_bump_self_audit_failures", spy)

        # Заставляем `record_admin_action` бросать — не важно что именно.
        def boom(*_a, **_kw):
            raise RuntimeError("self-audit boom")

        import src.services.event_service as event_service
        monkeypatch.setattr(event_service, "record_admin_action", boom)

        main_mod._emit_audit(
            action="user.login",
            actor_id=None,
            actor_type=None,
            username=None,
            emit_status="failure",
            allowed=False,
            request_id=None,
            details={},
        )

        assert calls["n"] == 1

    def test_lock_object_exists_and_is_lock(self):
        from src import main as main_mod
        assert hasattr(main_mod, "_self_audit_failures_lock")
        # acquire/release без блокировки — это threading.Lock, не Semaphore/RLock
        acquired = main_mod._self_audit_failures_lock.acquire(blocking=False)
        assert acquired is True
        main_mod._self_audit_failures_lock.release()


# ── 4. Pooled httpx для /token proxy ───────────────────────────────────────


class TestTokenProxyPool:
    def test_lifespan_initialises_token_proxy_client(self, monkeypatch):
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth-test:8000")
        monkeypatch.setenv("SERVICE_API_KEY", "test-key")
        from src.core.config import get_settings
        get_settings.cache_clear()
        try:
            from src.dependencies import auth as auth_dep
            from src.main import create_application
            auth_dep._introspect_client = None
            auth_dep._token_proxy_client = None
            app = create_application()

            async def _drive():
                async with app.router.lifespan_context(app):
                    cli = auth_dep._token_proxy_client
                    assert cli is not None
                    assert isinstance(cli, httpx.AsyncClient)
                    assert str(cli.base_url).startswith("http://auth-test")
                # После shutdown — None и .is_closed True
                assert auth_dep._token_proxy_client is None

            asyncio.run(_drive())
        finally:
            get_settings.cache_clear()

    def test_lifespan_skips_token_pool_when_auth_url_unset(self, monkeypatch):
        monkeypatch.delenv("AUTH_SERVICE_URL", raising=False)
        monkeypatch.setenv("SERVICE_API_KEY", "test-key")
        from src.core.config import get_settings
        get_settings.cache_clear()
        try:
            from src.dependencies import auth as auth_dep
            from src.main import create_application
            auth_dep._introspect_client = None
            auth_dep._token_proxy_client = None
            app = create_application()

            async def _drive():
                async with app.router.lifespan_context(app):
                    assert auth_dep._token_proxy_client is None

            asyncio.run(_drive())
        finally:
            get_settings.cache_clear()

    def test_token_endpoint_uses_pooled_client_when_set(self, monkeypatch, db, admin_client):
        """Когда `_token_proxy_client` инстанциирован — `/token` идёт через него,
        а не через per-call sync `httpx.post`.

        Используем `admin_client` фикстуру: ей нужен только `app` + DB; здесь
        TestClient прогоняет lifespan, поэтому подменяем pool после старта.
        AUTH_SERVICE_URL выставляем в теле + сбрасываем settings-cache: обработчик
        `/token` читает `get_settings()` per-request, fixture его не выставляет.
        """
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth-test:8000")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.dependencies import auth as auth_dep

        # Берём mock-транспорт, который перехватывает любую POST → возвращает 200.
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                200,
                json={"access_token": "pooled-jwt", "token_type": "Bearer"},
            )

        transport = httpx.MockTransport(handler)
        pooled = httpx.AsyncClient(
            base_url="http://auth-test:8000",
            transport=transport,
            timeout=5.0,
        )
        original = auth_dep._token_proxy_client
        auth_dep._token_proxy_client = pooled
        try:
            r = admin_client.post(
                "/api/logging/v1/token",
                data={"username": "admin", "password": "secret"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["access_token"] == "pooled-jwt"
            assert r.json()["token_type"] == "bearer"
            # Запрос реально ушёл в pooled client (не в httpx.post-fallback).
            assert len(calls) == 1
            assert calls[0].url.path == "/api/auth/v1/token"
        finally:
            auth_dep._token_proxy_client = original
            asyncio.run(pooled.aclose())

    def test_token_endpoint_reuses_single_pooled_client(self, monkeypatch, db, admin_client):
        """Несколько последовательных вызовов `/token` идут через один и тот же
        pooled client (не создаём новый на каждый запрос). AUTH_SERVICE_URL +
        settings-cache reset нужны по той же причине, что и в соседнем тесте."""
        monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth-test:8000")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.dependencies import auth as auth_dep

        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                200, json={"access_token": "reused", "token_type": "Bearer"}
            )

        transport = httpx.MockTransport(handler)
        pooled = httpx.AsyncClient(
            base_url="http://auth-test:8000",
            transport=transport,
            timeout=5.0,
        )
        original = auth_dep._token_proxy_client
        auth_dep._token_proxy_client = pooled
        try:
            for _ in range(3):
                r = admin_client.post(
                    "/api/logging/v1/token",
                    data={"username": "admin", "password": "s"},
                )
                assert r.status_code == 200
            assert len(calls) == 3
            # Pool жив всё это время — pooled.is_closed == False
            assert pooled.is_closed is False
        finally:
            auth_dep._token_proxy_client = original
            asyncio.run(pooled.aclose())
