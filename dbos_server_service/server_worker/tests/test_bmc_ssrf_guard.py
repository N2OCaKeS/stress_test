"""SSRF-guard для BMC endpoint'а.

`get_bmc_client` принимает host, который пришёл из server_service через
`endpoint_url` IPMI-controller'а. Без guard'а ничто не мешает оператору
прописать `127.0.0.1`, `169.254.169.254` (cloud-metadata) или `localhost` —
worker сходил бы туда HEAD'ом на `/redfish/v1/` и потенциально утянул
секреты (BMC creds в Basic-Auth header) к произвольному локальному сервису.

Guard живёт в `src.clients.ensure_bmc_host_allowed`:

* Резолвит host (если уже IP — берёт как есть).
* Отбивает loopback IPv4/IPv6 и link-local IPv4/IPv6 (включая metadata-range).
* Невозможно резолвить — тоже отбивает (`reason=resolve_failed`).
* Эмитит audit `bmc.endpoint_blocked` (WARNING) через outbox и поднимает
  `BmcEndpointBlockedError(BMC_ENDPOINT_BLOCKED)`.

Все тесты помечены `enforce_bmc_ssrf_guard`, чтобы conftest не подсовывал
no-op-замену.
"""

from __future__ import annotations

import socket

import pytest
from sqlalchemy import select

from src.clients import (
    BmcEndpointBlockedError,
    ensure_bmc_host_allowed,
    get_bmc_client,
)
from src.db.session import AsyncSessionLocal
from src.models import AuditOutbox

pytestmark = pytest.mark.enforce_bmc_ssrf_guard


async def _blocked_events() -> list[AuditOutbox]:
    async with AsyncSessionLocal() as session:
        stmt = select(AuditOutbox).order_by(AuditOutbox.id.asc())
        rows = list((await session.execute(stmt)).scalars().all())
    return [r for r in rows if (r.payload or {}).get("action") == "bmc.endpoint_blocked"]


class TestEnsureBmcHostAllowed:
    """Контракт самого guard'а (без get_bmc_client поверх)."""

    async def test_public_ipv4_allowed(self, monkeypatch):
        # Прямой IP — резолв не вызывается, проверяем чистый allow-path.
        await ensure_bmc_host_allowed("10.10.0.5")
        await ensure_bmc_host_allowed("10.10.0.5:443")
        # IPv6 в bracket-нотации с портом.
        await ensure_bmc_host_allowed("[2001:db8::1]:8443")

    async def test_hostname_resolving_to_public_allowed(self, monkeypatch):
        def fake_getaddrinfo(host, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.10.0.5", 0))]
        monkeypatch.setattr("src.clients.socket.getaddrinfo", fake_getaddrinfo)
        await ensure_bmc_host_allowed("bmc.example.test")

    async def test_loopback_ipv4_blocked(self):
        with pytest.raises(BmcEndpointBlockedError) as excinfo:
            await ensure_bmc_host_allowed("127.0.0.1")
        assert excinfo.value.error_code == "BMC_ENDPOINT_BLOCKED"
        assert excinfo.value.details["reason"] == "loopback"
        events = await _blocked_events()
        assert len(events) == 1
        assert events[0].payload["details"]["reason"] == "loopback"
        assert events[0].payload["severity"] == "WARNING"

    async def test_loopback_ipv4_with_port_blocked(self):
        with pytest.raises(BmcEndpointBlockedError):
            await ensure_bmc_host_allowed("127.0.0.1:8000")

    async def test_loopback_ipv6_blocked(self):
        with pytest.raises(BmcEndpointBlockedError) as excinfo:
            await ensure_bmc_host_allowed("[::1]:443")
        assert excinfo.value.details["reason"] == "loopback"

    async def test_link_local_ipv4_metadata_blocked(self):
        with pytest.raises(BmcEndpointBlockedError) as excinfo:
            await ensure_bmc_host_allowed("169.254.169.254")
        assert excinfo.value.error_code == "BMC_ENDPOINT_BLOCKED"
        assert excinfo.value.details["reason"] == "link_local"

    async def test_link_local_ipv6_blocked(self):
        with pytest.raises(BmcEndpointBlockedError) as excinfo:
            await ensure_bmc_host_allowed("[fe80::1]")
        assert excinfo.value.details["reason"] == "link_local"

    async def test_hostname_resolving_to_loopback_blocked(self, monkeypatch):
        """`localhost` обычно резолвится в `127.0.0.1` или `::1` — блок."""
        def fake_getaddrinfo(host, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
        monkeypatch.setattr("src.clients.socket.getaddrinfo", fake_getaddrinfo)

        with pytest.raises(BmcEndpointBlockedError) as excinfo:
            await ensure_bmc_host_allowed("localhost")
        assert excinfo.value.details["reason"] == "loopback"
        assert excinfo.value.details["resolved"] == "127.0.0.1"

    async def test_unresolvable_hostname_blocked(self, monkeypatch):
        def fake_getaddrinfo(*args, **kwargs):
            raise socket.gaierror(-2, "Name or service not known")
        monkeypatch.setattr("src.clients.socket.getaddrinfo", fake_getaddrinfo)

        with pytest.raises(BmcEndpointBlockedError) as excinfo:
            await ensure_bmc_host_allowed("does-not-resolve.invalid")
        assert excinfo.value.details["reason"] == "resolve_failed"
        events = await _blocked_events()
        assert any(e.payload["details"]["reason"] == "resolve_failed" for e in events)

    async def test_empty_host_blocked(self):
        with pytest.raises(BmcEndpointBlockedError) as excinfo:
            await ensure_bmc_host_allowed("")
        assert excinfo.value.details["reason"] == "empty_host"

    async def test_public_ipv4_passes_for_get_bmc_client(self, monkeypatch):
        """Sanity: public IP проходит guard, и `get_bmc_client` идёт дальше."""
        from src.clients import IpmitoolClient
        # Probe-cascade в False — `get_bmc_client` отдаст ipmitool без HTTP.
        async def fake_cascade(host):
            from src.clients import ProbeResult
            return ProbeResult(reachable=False, scheme="https", verify_tls=True)
        monkeypatch.setattr("src.clients._probe_redfish_cascade", fake_cascade)

        client = await get_bmc_client(
            host="10.10.0.5", username="u", password="p",
        )
        assert isinstance(client, IpmitoolClient)


class TestGetBmcClientBlocksLocalEndpoints:
    """Интеграционные ветки: `get_bmc_client(host=loopback)` фейлится до probe'а."""

    async def test_get_bmc_client_blocks_loopback(self, monkeypatch):
        # Если guard сломан — probe-cascade вызовется и отдаст ipmitool;
        # вместо этого ожидаем raise до probe'а. Подменяем cascade на sentinel,
        # который кричит «меня не должны были позвать».
        async def must_not_call(_host):  # pragma: no cover
            raise AssertionError("probe must not be reached when host is blocked")
        monkeypatch.setattr("src.clients._probe_redfish_cascade", must_not_call)

        with pytest.raises(BmcEndpointBlockedError):
            await get_bmc_client(host="127.0.0.1", username="u", password="p")

    async def test_get_bmc_client_blocks_metadata_ip(self, monkeypatch):
        async def must_not_call(_host):  # pragma: no cover
            raise AssertionError("probe must not be reached")
        monkeypatch.setattr("src.clients._probe_redfish_cascade", must_not_call)

        with pytest.raises(BmcEndpointBlockedError):
            await get_bmc_client(
                host="169.254.169.254", username="u", password="p",
            )

    async def test_get_bmc_client_blocks_localhost_hostname(self, monkeypatch):
        async def must_not_call(_host):  # pragma: no cover
            raise AssertionError("probe must not be reached")
        monkeypatch.setattr("src.clients._probe_redfish_cascade", must_not_call)

        # Реалистичный резолв localhost: на CI бывает только IPv6, гарантируем
        # IPv4-loopback явно.
        def fake_getaddrinfo(host, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
        monkeypatch.setattr("src.clients.socket.getaddrinfo", fake_getaddrinfo)

        with pytest.raises(BmcEndpointBlockedError):
            await get_bmc_client(host="localhost", username="u", password="p")

    async def test_get_bmc_client_blocks_unresolvable(self, monkeypatch):
        async def must_not_call(_host):  # pragma: no cover
            raise AssertionError("probe must not be reached")
        monkeypatch.setattr("src.clients._probe_redfish_cascade", must_not_call)

        def fake_getaddrinfo(*args, **kwargs):
            raise socket.gaierror(-2, "Name or service not known")
        monkeypatch.setattr("src.clients.socket.getaddrinfo", fake_getaddrinfo)

        with pytest.raises(BmcEndpointBlockedError):
            await get_bmc_client(
                host="nope.invalid", username="u", password="p",
            )

    async def test_get_bmc_client_blocks_in_ipmitool_branch_too(self, monkeypatch):
        """`prefer='ipmitool'` тоже проходит guard, не только Redfish-ветка."""
        with pytest.raises(BmcEndpointBlockedError):
            await get_bmc_client(
                host="127.0.0.1", username="u", password="p", prefer="ipmitool",
            )
