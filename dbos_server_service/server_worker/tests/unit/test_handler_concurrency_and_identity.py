"""Unit-тесты: handler concurrency + service-identity header + sweep tunables.

Покрытие:

* `WORKER_HANDLER_CONCURRENCY` парсится в settings (default 4, bounds 1..64).
* `server_service_client._headers()` отдаёт `X-Service-Identity: server_worker`
  — defense-in-depth, симметрично `audit_client.emit`.
* `worker_orphan_threshold_seconds` и `worker_heartbeat_stale_seconds`
  читаются из новых env-имён (`SWEEP_ORPHAN_THRESHOLD_SECONDS` /
  `SWEEP_HEARTBEAT_TIMEOUT_SECONDS`).
* `_get_handler_semaphore()` инициализируется лениво и кешируется.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from src.core.config import Settings, get_settings
from src.services import http_pool, server_service_client


# ── handler concurrency settings ────────────────────────────────────────────


class TestHandlerConcurrencyConfig:
    def test_default_is_4(self, monkeypatch):
        """Дефолт 4 — settings без env-override."""
        monkeypatch.delenv("WORKER_HANDLER_CONCURRENCY", raising=False)
        s = Settings(
            database_url="postgresql+psycopg://x/y",
            server_service_url="http://s",
            auth_service_url="http://a",
            logging_service_url="http://l",
            worker_bot_token="t",
        )
        assert s.worker_handler_concurrency == 4

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("WORKER_HANDLER_CONCURRENCY", "16")
        s = Settings(
            database_url="postgresql+psycopg://x/y",
            server_service_url="http://s",
            auth_service_url="http://a",
            logging_service_url="http://l",
            worker_bot_token="t",
        )
        assert s.worker_handler_concurrency == 16

    def test_lower_bound(self, monkeypatch):
        """ge=1 — ноль и отрицательные значения отбиваются."""
        monkeypatch.setenv("WORKER_HANDLER_CONCURRENCY", "0")
        with pytest.raises(Exception):
            Settings(
                database_url="postgresql+psycopg://x/y",
                server_service_url="http://s",
                auth_service_url="http://a",
                logging_service_url="http://l",
                worker_bot_token="t",
            )

    def test_upper_bound(self, monkeypatch):
        """le=64 — за лимитом httpx/redis пулы всё равно начнут давиться."""
        monkeypatch.setenv("WORKER_HANDLER_CONCURRENCY", "65")
        with pytest.raises(Exception):
            Settings(
                database_url="postgresql+psycopg://x/y",
                server_service_url="http://s",
                auth_service_url="http://a",
                logging_service_url="http://l",
                worker_bot_token="t",
            )


# ── sweep tunables ───────────────────────────────────────────────────────────


class TestSweepTunables:
    def test_orphan_threshold_default_is_180(self, monkeypatch):
        """Дефолт уменьшён 1800→180s — recovery укладывается в ~3 мин."""
        for env in (
            "SWEEP_ORPHAN_THRESHOLD_SECONDS",
            "WORKER_ORPHAN_THRESHOLD_SECONDS",
        ):
            monkeypatch.delenv(env, raising=False)
        s = Settings(
            database_url="postgresql+psycopg://x/y",
            server_service_url="http://s",
            auth_service_url="http://a",
            logging_service_url="http://l",
            worker_bot_token="t",
        )
        assert s.worker_orphan_threshold_seconds == 180.0

    def test_heartbeat_stale_default_is_60(self, monkeypatch):
        """Дефолт уменьшён 300→60s — пропущенный heartbeat-tick = inactive."""
        for env in (
            "SWEEP_HEARTBEAT_TIMEOUT_SECONDS",
            "WORKER_HEARTBEAT_STALE_SECONDS",
        ):
            monkeypatch.delenv(env, raising=False)
        s = Settings(
            database_url="postgresql+psycopg://x/y",
            server_service_url="http://s",
            auth_service_url="http://a",
            logging_service_url="http://l",
            worker_bot_token="t",
        )
        assert s.worker_heartbeat_stale_seconds == 60.0

    def test_orphan_threshold_env_alias(self, monkeypatch):
        monkeypatch.setenv("SWEEP_ORPHAN_THRESHOLD_SECONDS", "600")
        s = Settings(
            database_url="postgresql+psycopg://x/y",
            server_service_url="http://s",
            auth_service_url="http://a",
            logging_service_url="http://l",
            worker_bot_token="t",
        )
        assert s.worker_orphan_threshold_seconds == 600.0

    def test_heartbeat_stale_env_alias(self, monkeypatch):
        monkeypatch.setenv("SWEEP_HEARTBEAT_TIMEOUT_SECONDS", "120")
        s = Settings(
            database_url="postgresql+psycopg://x/y",
            server_service_url="http://s",
            auth_service_url="http://a",
            logging_service_url="http://l",
            worker_bot_token="t",
        )
        assert s.worker_heartbeat_stale_seconds == 120.0


# ── X-Service-Identity в server_service_client ──────────────────────────────


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response, capture_kwargs=None):
        self._response = response
        self._capture = capture_kwargs

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, headers=None, params=None):
        if self._capture is not None:
            self._capture.update(url=url, headers=headers or {})
        return self._response

    async def post(self, url, headers=None, json=None, params=None):
        if self._capture is not None:
            self._capture.update(url=url, headers=headers or {}, json=json)
        return self._response


@pytest.fixture(autouse=True)
def _reset_http_pool():
    http_pool.reset_for_tests()
    yield
    http_pool.reset_for_tests()


@pytest.fixture
def settings_stub(monkeypatch):
    class _S:
        server_service_url = "http://srv.test"
        worker_bot_token = "wbt-1"
        http_request_timeout_seconds = 5.0

    monkeypatch.setattr(
        "src.services.server_service_client.get_settings", lambda: _S()
    )
    return _S


class TestServiceIdentityHeader:
    async def test_get_includes_service_identity(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(
            httpx, "AsyncClient", _Client(_Resp(200, {"kind": "idrac"}), cap)
        )
        await server_service_client.fetch_ipmi_credentials("srv_1")
        assert cap["headers"].get("X-Service-Identity") == "server_worker"

    async def test_post_includes_service_identity(self, settings_stub, monkeypatch):
        cap = {}
        monkeypatch.setattr(
            httpx, "AsyncClient", _Client(_Resp(200, {"ok": True}), cap)
        )
        await server_service_client.submit_rotated_password(
            "srv_1", "acc_1", "secret"
        )
        assert cap["headers"].get("X-Service-Identity") == "server_worker"

    async def test_service_identity_alongside_target_department(
        self, settings_stub, monkeypatch
    ):
        """Оба header'а должны сосуществовать — `X-Service-Identity`
        — defense-in-depth, `X-Target-Department-Id` — cross-tenant cross-check."""
        cap = {}
        monkeypatch.setattr(
            httpx, "AsyncClient", _Client(_Resp(200, {"kind": "idrac"}), cap)
        )
        await server_service_client.fetch_ipmi_credentials("srv_1", "dep_xyz")
        assert cap["headers"].get("X-Service-Identity") == "server_worker"
        assert cap["headers"].get("X-Target-Department-Id") == "dep_xyz"


# ── handler semaphore lazy init ─────────────────────────────────────────────


class TestHandlerSemaphore:
    async def test_lazy_init_uses_settings(self, monkeypatch):
        """Semaphore создаётся при первом вызове, читая лимит из settings."""
        from src.tasks import _runner

        _runner._reset_handler_semaphore_for_tests()

        class _S:
            worker_handler_concurrency = 3

        monkeypatch.setattr(_runner, "get_settings", lambda: _S())
        sem = _runner._get_handler_semaphore()
        assert isinstance(sem, asyncio.Semaphore)
        # asyncio.Semaphore хранит счётчик в `_value`; начинаем с лимита.
        assert sem._value == 3
        # Повторный вызов возвращает тот же экземпляр.
        assert _runner._get_handler_semaphore() is sem
        _runner._reset_handler_semaphore_for_tests()

    async def test_semaphore_actually_serializes(self, monkeypatch):
        """Bounded concurrency: лимит=1 → второй coroutine ждёт первого."""
        from src.tasks import _runner

        _runner._reset_handler_semaphore_for_tests()

        class _S:
            worker_handler_concurrency = 1

        monkeypatch.setattr(_runner, "get_settings", lambda: _S())

        order = []
        gate = asyncio.Event()

        async def slow_holder():
            async with _runner._get_handler_semaphore():
                order.append("hold-start")
                await gate.wait()
                order.append("hold-end")

        async def waiter():
            async with _runner._get_handler_semaphore():
                order.append("waiter-acquired")

        # Запускаем оба: первый занимает семафор и блокируется на gate,
        # второй должен застрять на acquire до того, как gate.set() освободит.
        t1 = asyncio.create_task(slow_holder())
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)
        # Второй ещё не зашёл — семафор занят первым.
        assert order == ["hold-start"]
        gate.set()
        await asyncio.gather(t1, t2)
        assert order == ["hold-start", "hold-end", "waiter-acquired"]
        _runner._reset_handler_semaphore_for_tests()
