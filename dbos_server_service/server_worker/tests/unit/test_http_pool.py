"""Unit-тесты `src/services/http_pool.py`.

Покрытие:

* `get_audit_client` / `get_server_service_client` — один и тот же
  объект на повторных вызовах (нет per-call instantiation);
* `aclose_all` закрывает оба пула и зануляет слоты, чтобы следующий
  `get_*` создал свежий клиент;
* `reset_for_tests` синхронно сбрасывает слоты без `aclose`;
* `emit()` использует pool — повторные вызовы НЕ создают новый
  `httpx.AsyncClient`;
* `server_service_client` callback'и тоже используют pool.
"""

from __future__ import annotations

import httpx
import pytest

from src.services import audit_client, http_pool, server_service_client


@pytest.fixture(autouse=True)
def _reset_pool():
    http_pool.reset_for_tests()
    yield
    http_pool.reset_for_tests()


class TestPoolIdentity:
    """Один пул на процесс — повторные get_* возвращают тот же объект."""

    def test_audit_pool_is_singleton(self):
        a = http_pool.get_audit_client()
        b = http_pool.get_audit_client()
        assert a is b

    def test_server_service_pool_is_singleton(self):
        a = http_pool.get_server_service_client()
        b = http_pool.get_server_service_client()
        assert a is b

    def test_pools_are_separate_instances(self):
        """Разные каналы — разные клиенты (разные лимиты, разные FD-учёты)."""
        a = http_pool.get_audit_client()
        s = http_pool.get_server_service_client()
        assert a is not s


class TestPoolLifecycle:
    async def test_aclose_all_closes_clients(self):
        a = http_pool.get_audit_client()
        s = http_pool.get_server_service_client()
        await http_pool.aclose_all()
        assert a.is_closed
        assert s.is_closed

    async def test_get_after_aclose_creates_new(self):
        """После shutdown'а новый цикл startup получает свежий клиент."""
        a1 = http_pool.get_audit_client()
        await http_pool.aclose_all()
        a2 = http_pool.get_audit_client()
        assert a2 is not a1
        assert not a2.is_closed

    async def test_aclose_all_is_idempotent(self):
        """Двойной aclose не должен падать."""
        http_pool.get_audit_client()
        await http_pool.aclose_all()
        # повторный — без падений
        await http_pool.aclose_all()

    async def test_aclose_all_redacts_exception_message(self, monkeypatch, caplog):
        """Если aclose() бросит исключение с URL-секретом в repr — лог должен
        пройти через `redact_error_message`, не утечь password.

        Симметрия с W15-фиксом CLI (там та же дыра была закрыта в shutdown-логе).
        """
        import logging

        class _BoomClient:
            def __init__(self, **kwargs):
                self.is_closed = False

            async def aclose(self):
                raise RuntimeError(
                    "broken pipe to "
                    "https://user:hunter2_topsecret@logging.local/api/ingest"
                )

        monkeypatch.setattr("src.services.http_pool.httpx.AsyncClient", _BoomClient)

        class _S:
            audit_pool_max_connections = 20
            audit_pool_max_keepalive_connections = 10
            server_service_pool_max_connections = 20
            server_service_pool_max_keepalive_connections = 10
            http_request_timeout_seconds = 5.0

        monkeypatch.setattr("src.services.http_pool.get_settings", lambda: _S())

        http_pool.get_audit_client()
        with caplog.at_level(logging.WARNING, logger="src.services.http_pool"):
            await http_pool.aclose_all()

        log_blob = " ".join(rec.getMessage() for rec in caplog.records)
        assert "hunter2_topsecret" not in log_blob, (
            "URL-пароль не должен утекать через shutdown-лог"
        )
        # Сам факт ошибки должен светиться (RuntimeError type).
        assert "RuntimeError" in log_blob

    def test_reset_for_tests_drops_cache_without_aclose(self):
        a1 = http_pool.get_audit_client()
        http_pool.reset_for_tests()
        a2 = http_pool.get_audit_client()
        assert a1 is not a2


class TestAuditClientUsesPool:
    """`emit()` дёргает pool, а не создаёт новый httpx-клиент на каждый call."""

    async def test_emit_reuses_pooled_client(self, monkeypatch):
        constructed: list[dict] = []

        class _FakeClient:
            def __init__(self, **kwargs):
                constructed.append(kwargs)
                self.is_closed = False

            async def post(self, url, json=None, headers=None):
                class _R:
                    status_code = 201
                return _R()

            async def aclose(self):
                self.is_closed = True

        monkeypatch.setattr("src.services.http_pool.httpx.AsyncClient", _FakeClient)

        class _S:
            logging_service_url = "http://logging.test"
            logging_service_api_key = "k"
            worker_bot_token = ""
            http_request_timeout_seconds = 5.0
            audit_pool_max_connections = 20
            audit_pool_max_keepalive_connections = 10
            server_service_pool_max_connections = 20
            server_service_pool_max_keepalive_connections = 10

        monkeypatch.setattr("src.services.audit_client.get_settings", lambda: _S())
        monkeypatch.setattr("src.services.http_pool.get_settings", lambda: _S())

        await audit_client.emit("x.y")
        await audit_client.emit("x.y")
        await audit_client.emit("x.y")

        # Один client конструируется на всю серию emit'ов.
        assert len(constructed) == 1


class TestServerServiceClientUsesPool:
    async def test_fetch_ipmi_reuses_pooled_client(self, monkeypatch):
        constructed: list[dict] = []

        class _Resp:
            status_code = 200
            def json(self):
                return {"kind": "idrac", "username": "u", "password": "p"}

        class _FakeClient:
            def __init__(self, **kwargs):
                constructed.append(kwargs)
                self.is_closed = False

            async def get(self, url, headers=None):
                return _Resp()

            async def aclose(self):
                self.is_closed = True

        monkeypatch.setattr("src.services.http_pool.httpx.AsyncClient", _FakeClient)

        class _S:
            server_service_url = "http://srv.test"
            worker_bot_token = "wbt-1"
            http_request_timeout_seconds = 5.0
            audit_pool_max_connections = 20
            audit_pool_max_keepalive_connections = 10
            server_service_pool_max_connections = 20
            server_service_pool_max_keepalive_connections = 10

        monkeypatch.setattr("src.services.server_service_client.get_settings", lambda: _S())
        monkeypatch.setattr("src.services.http_pool.get_settings", lambda: _S())

        await server_service_client.fetch_ipmi_credentials("srv_1")
        await server_service_client.fetch_ipmi_credentials("srv_2")
        await server_service_client.fetch_ipmi_credentials("srv_3")

        assert len(constructed) == 1


class TestPoolConfig:
    """Лимиты пула берутся из настроек."""

    def test_audit_pool_uses_settings_limits(self, monkeypatch):
        captured: list[dict] = []

        class _FakeClient:
            def __init__(self, **kwargs):
                captured.append(kwargs)
                self.is_closed = False

            async def aclose(self):
                self.is_closed = True

        monkeypatch.setattr("src.services.http_pool.httpx.AsyncClient", _FakeClient)

        class _S:
            audit_pool_max_connections = 42
            audit_pool_max_keepalive_connections = 7
            server_service_pool_max_connections = 99
            server_service_pool_max_keepalive_connections = 33
            http_request_timeout_seconds = 12.5

        monkeypatch.setattr("src.services.http_pool.get_settings", lambda: _S())

        http_pool.get_audit_client()
        limits = captured[0]["limits"]
        assert isinstance(limits, httpx.Limits)
        assert limits.max_connections == 42
        assert limits.max_keepalive_connections == 7
        assert captured[0]["timeout"] == 12.5

    def test_server_service_pool_uses_settings_limits(self, monkeypatch):
        captured: list[dict] = []

        class _FakeClient:
            def __init__(self, **kwargs):
                captured.append(kwargs)
                self.is_closed = False

            async def aclose(self):
                self.is_closed = True

        monkeypatch.setattr("src.services.http_pool.httpx.AsyncClient", _FakeClient)

        class _S:
            audit_pool_max_connections = 20
            audit_pool_max_keepalive_connections = 10
            server_service_pool_max_connections = 99
            server_service_pool_max_keepalive_connections = 33
            http_request_timeout_seconds = 7.0

        monkeypatch.setattr("src.services.http_pool.get_settings", lambda: _S())

        http_pool.get_server_service_client()
        limits = captured[0]["limits"]
        assert limits.max_connections == 99
        assert limits.max_keepalive_connections == 33
        assert captured[0]["timeout"] == 7.0


class TestBmcProbePool:
    """BMC scheme-probe pool: один клиент на (scheme, verify) комбинацию."""

    def test_probe_client_is_singleton_per_key(self):
        a1 = http_pool.get_bmc_probe_client(scheme="https", verify=True)
        a2 = http_pool.get_bmc_probe_client(scheme="https", verify=True)
        assert a1 is a2

    def test_probe_clients_differ_by_verify(self):
        a = http_pool.get_bmc_probe_client(scheme="https", verify=True)
        b = http_pool.get_bmc_probe_client(scheme="https", verify=False)
        assert a is not b

    def test_probe_clients_differ_by_scheme(self):
        a = http_pool.get_bmc_probe_client(scheme="https", verify=True)
        b = http_pool.get_bmc_probe_client(scheme="http", verify=True)
        assert a is not b

    def test_probe_http_ignores_verify(self):
        """Для plain-HTTP флаг verify ничего не значит — один клиент."""
        a = http_pool.get_bmc_probe_client(scheme="http", verify=True)
        b = http_pool.get_bmc_probe_client(scheme="http", verify=False)
        assert a is b

    def test_probe_rejects_unsupported_scheme(self):
        with pytest.raises(ValueError):
            http_pool.get_bmc_probe_client(scheme="ftp", verify=True)

    async def test_aclose_all_closes_probe_clients(self):
        a = http_pool.get_bmc_probe_client(scheme="https", verify=True)
        b = http_pool.get_bmc_probe_client(scheme="https", verify=False)
        await http_pool.aclose_all()
        assert a.is_closed
        assert b.is_closed

    async def test_get_after_aclose_creates_new_probe(self):
        a1 = http_pool.get_bmc_probe_client(scheme="https", verify=True)
        await http_pool.aclose_all()
        a2 = http_pool.get_bmc_probe_client(scheme="https", verify=True)
        assert a1 is not a2


class TestBmcRedfishTransport:
    """Shared transport под per-host RedfishClient — один на verify-уровень."""

    def test_transport_is_singleton_per_verify(self):
        a1 = http_pool.get_bmc_redfish_transport(verify=True)
        a2 = http_pool.get_bmc_redfish_transport(verify=True)
        assert a1 is a2

    def test_transports_differ_by_verify(self):
        a = http_pool.get_bmc_redfish_transport(verify=True)
        b = http_pool.get_bmc_redfish_transport(verify=False)
        assert a is not b

    async def test_aclose_all_closes_transports(self):
        http_pool.get_bmc_redfish_transport(verify=True)
        http_pool.get_bmc_redfish_transport(verify=False)
        # Не падает на закрытии — основная проверка.
        await http_pool.aclose_all()
        # После shutdown'а слот пустой → следующий get создаёт новый.
        a2 = http_pool.get_bmc_redfish_transport(verify=True)
        assert a2 is not None
