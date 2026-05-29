"""`redfish_timeout_seconds` / `redfish_verify_tls` пробрасываются в RedfishClient.

До фикса `get_bmc_client` строил `RedfishClient(host, username, password)`
без `timeout=` и `verify_tls=` — оба настраиваются в env, но не доезжали
до transport'а: probe их видел, а реальный HTTP-клиент сидел на hard-coded
дефолтах (`timeout=30.0`, `verify_tls=False`). После фикса оба значения
читаются из `Settings` и попадают в конструктор.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


class TestRedfishClientKwargsFromSettings:
    async def test_redfish_client_receives_settings_kwargs(self, monkeypatch):
        """RedfishClient(...) получает `verify_tls` и `timeout` из settings."""
        import src.clients as clients_mod
        from src.core import config as cfg

        cfg.get_settings.cache_clear()
        monkeypatch.setenv("REDFISH_VERIFY_TLS", "true")
        monkeypatch.setenv("REDFISH_TIMEOUT_SECONDS", "12.5")

        async def fake_probe(host: str) -> bool:
            return True

        monkeypatch.setattr(clients_mod, "_probe_redfish_cascade", fake_probe)

        captured: dict = {}

        class FakeRedfish:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def aclose(self):
                pass

        monkeypatch.setattr(clients_mod, "RedfishClient", FakeRedfish)

        try:
            await clients_mod.get_bmc_client(
                host="bmc.test",
                username="u",
                password="p",
            )
            assert captured["verify_tls"] is True
            assert captured["timeout"] == pytest.approx(12.5)
            assert captured["host"] == "bmc.test"
            assert captured["username"] == "u"
            assert captured["password"] == "p"
        finally:
            cfg.get_settings.cache_clear()

    async def test_redfish_client_default_kwargs(self, monkeypatch):
        """Без env-override: verify_tls=False, timeout=30.0 (дефолты settings)."""
        import src.clients as clients_mod
        from src.core import config as cfg

        cfg.get_settings.cache_clear()
        monkeypatch.delenv("REDFISH_VERIFY_TLS", raising=False)
        monkeypatch.delenv("REDFISH_TIMEOUT_SECONDS", raising=False)

        async def fake_probe(host: str) -> bool:
            return True

        monkeypatch.setattr(clients_mod, "_probe_redfish_cascade", fake_probe)

        captured: dict = {}

        class FakeRedfish:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def aclose(self):
                pass

        monkeypatch.setattr(clients_mod, "RedfishClient", FakeRedfish)

        try:
            await clients_mod.get_bmc_client(
                host="bmc.test",
                username="u",
                password="p",
            )
            assert captured["verify_tls"] is False
            assert captured["timeout"] == pytest.approx(30.0)
        finally:
            cfg.get_settings.cache_clear()
