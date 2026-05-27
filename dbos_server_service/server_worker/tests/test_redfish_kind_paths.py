"""Unit-тесты kind-aware Redfish manager-paths.

Покрытие:

* `resolve_manager_id(kind)` — мэппинг kind → manager-id-segment.
* `RedfishClient(manager_id="...")` — explicit path для iDRAC и iLO,
  PATCH-target адресует правильный `/Managers/<kind-id>/Accounts/<n>`.
* `RedfishClient(manager_id="")` — discovery через коллекцию
  `/redfish/v1/Managers`, берётся первый Member, кэшируется.
* discovery edge-case'ы: пустой `Members`, отсутствующий `@odata.id`,
  не-объект Members[0] → `RedfishError`.

HTTP-слой — `httpx.MockTransport`. Никакого реального BMC.
"""

from __future__ import annotations

import httpx
import pytest

from src.clients.redfish import (
    KIND_MANAGER_IDS,
    RedfishClient,
    RedfishError,
    resolve_manager_id,
)


def _make_client(handler, *, manager_id: str = "iDRAC.Embedded.1") -> RedfishClient:
    """Собрать RedfishClient с подменённым MockTransport и custom manager_id."""
    transport = httpx.MockTransport(handler)
    client = RedfishClient(
        host="https://bmc.test",
        username="root",
        password="Calvin",
        manager_id=manager_id,
    )
    client._client = httpx.AsyncClient(
        base_url="https://bmc.test",
        auth=("root", "Calvin"),
        timeout=5.0,
        transport=transport,
    )
    return client


def _ok(status: int = 200, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {})


# ── resolve_manager_id ───────────────────────────────────────────────────────


class TestResolveManagerId:
    def test_idrac_returns_embedded(self):
        assert resolve_manager_id("idrac") == "iDRAC.Embedded.1"

    def test_ilo_returns_1(self):
        assert resolve_manager_id("ilo") == "1"

    def test_ipmi_returns_empty(self):
        assert resolve_manager_id("ipmi") == ""

    def test_redfish_returns_empty(self):
        assert resolve_manager_id("redfish") == ""

    def test_unknown_returns_empty(self):
        """Любой неизвестный kind → discovery, не падаем."""
        assert resolve_manager_id("hpe_xyz") == ""

    def test_none_returns_empty(self):
        assert resolve_manager_id(None) == ""

    def test_mapping_keys_are_explicit_kinds(self):
        """Явный manager-path только для idrac/ilo; остальные kind — discovery."""
        assert set(KIND_MANAGER_IDS.keys()) == {"idrac", "ilo"}


# ── PATCH manager-path по kind ───────────────────────────────────────────────


class TestRotateUserPasswordPerKind:
    async def test_idrac_path(self):
        """iDRAC → PATCH /redfish/v1/Managers/iDRAC.Embedded.1/Accounts/2."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            return _ok(200)

        async with _make_client(handler, manager_id="iDRAC.Embedded.1") as c:
            await c.rotate_user_password(2, "NewPass!23")
        assert captured["path"] == "/redfish/v1/Managers/iDRAC.Embedded.1/Accounts/2"

    async def test_ilo_path(self):
        """iLO → PATCH /redfish/v1/Managers/1/Accounts/2."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            return _ok(200)

        async with _make_client(handler, manager_id="1") as c:
            await c.rotate_user_password(2, "p")
        assert captured["path"] == "/redfish/v1/Managers/1/Accounts/2"


# ── Discovery /Managers (manager_id="") ──────────────────────────────────────


class TestManagersDiscovery:
    async def test_discovery_picks_first_member(self):
        """manager_id='' → GET /Managers → берём last segment первого Member."""
        captured: dict = {"calls": []}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["calls"].append(request.url.path)
            if request.url.path == "/redfish/v1/Managers":
                return _ok(200, {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Managers/BMC.0"},
                    ],
                })
            # Второй вызов — PATCH к Accounts на discovered manager.
            return _ok(200)

        async with _make_client(handler, manager_id="") as c:
            await c.rotate_user_password(3, "p")

        assert "/redfish/v1/Managers" in captured["calls"]
        # Discovered id попал в PATCH-path
        patch_call = next(
            p for p in captured["calls"] if "Accounts" in p
        )
        assert patch_call == "/redfish/v1/Managers/BMC.0/Accounts/3"

    async def test_discovery_cached_for_subsequent_calls(self):
        """Discovery один раз — последующие вызовы не дёргают коллекцию."""
        captured: dict = {"managers_calls": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/redfish/v1/Managers":
                captured["managers_calls"] += 1
                return _ok(200, {
                    "Members": [
                        {"@odata.id": "/redfish/v1/Managers/Self"},
                    ],
                })
            return _ok(200)

        async with _make_client(handler, manager_id="") as c:
            await c.rotate_user_password(1, "a")
            await c.rotate_user_password(2, "b")
            await c.rotate_user_password(3, "c")

        assert captured["managers_calls"] == 1

    async def test_discovery_empty_members_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {"Members": []})

        async with _make_client(handler, manager_id="") as c:
            with pytest.raises(RedfishError) as exc:
                await c.rotate_user_password(1, "x")
            assert "empty" in exc.value.message.lower()

    async def test_discovery_missing_members_field_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {"Name": "no members here"})

        async with _make_client(handler, manager_id="") as c:
            with pytest.raises(RedfishError):
                await c.rotate_user_password(1, "x")

    async def test_discovery_member_missing_odata_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {"Members": [{"Name": "no-id"}]})

        async with _make_client(handler, manager_id="") as c:
            with pytest.raises(RedfishError) as exc:
                await c.rotate_user_password(1, "x")
            assert "@odata.id" in exc.value.message

    async def test_discovery_non_dict_member_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _ok(200, {"Members": ["not-a-dict"]})

        async with _make_client(handler, manager_id="") as c:
            with pytest.raises(RedfishError):
                await c.rotate_user_password(1, "x")


# ── get_bmc_client пропускает kind в RedfishClient ──────────────────────────


class TestGetBmcClientForwardsKind:
    async def test_idrac_kind_uses_embedded_path(self, monkeypatch):
        """`get_bmc_client(kind='idrac')` → клиент с iDRAC.Embedded.1."""
        from src.clients import get_bmc_client

        async def fake_probe(host: str, *, scheme: str = "https") -> bool:
            return True

        monkeypatch.setattr("src.clients._probe_redfish", fake_probe)
        client = await get_bmc_client(
            host="bmc.test",
            username="u",
            password="p",
            kind="idrac",
        )
        try:
            assert isinstance(client, RedfishClient)
            assert client._manager_id == "iDRAC.Embedded.1"
        finally:
            await client.aclose()

    async def test_ilo_kind_uses_one_path(self, monkeypatch):
        from src.clients import get_bmc_client

        async def fake_probe(host: str, *, scheme: str = "https") -> bool:
            return True

        monkeypatch.setattr("src.clients._probe_redfish", fake_probe)
        client = await get_bmc_client(
            host="bmc.test",
            username="u",
            password="p",
            kind="ilo",
        )
        try:
            assert client._manager_id == "1"
        finally:
            await client.aclose()

    async def test_ipmi_kind_uses_empty_for_discovery(self, monkeypatch):
        from src.clients import get_bmc_client

        async def fake_probe(host: str, *, scheme: str = "https") -> bool:
            return True

        monkeypatch.setattr("src.clients._probe_redfish", fake_probe)
        client = await get_bmc_client(
            host="bmc.test",
            username="u",
            password="p",
            kind="ipmi",
        )
        try:
            assert client._manager_id == ""
        finally:
            await client.aclose()

    async def test_no_kind_keeps_default(self, monkeypatch):
        """Legacy-path без kind: клиент получает default iDRAC."""
        from src.clients import get_bmc_client

        async def fake_probe(host: str, *, scheme: str = "https") -> bool:
            return True

        monkeypatch.setattr("src.clients._probe_redfish", fake_probe)
        client = await get_bmc_client(
            host="bmc.test",
            username="u",
            password="p",
        )
        try:
            assert client._manager_id == "iDRAC.Embedded.1"
        finally:
            await client.aclose()


# ── _bmc_helpers.get_bmc передаёт kind из creds ─────────────────────────────


class TestBmcHelperGetBmc:
    async def test_get_bmc_forwards_kind_from_creds(self, monkeypatch):
        from src.tasks import _bmc_helpers

        captured: dict = {}

        async def fake_get_bmc_client(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(_bmc_helpers, "get_bmc_client", fake_get_bmc_client)
        await _bmc_helpers.get_bmc({
            "endpoint_url": "https://bmc.test",
            "username": "u",
            "password": "p",
            "kind": "ilo",
        })
        assert captured["kind"] == "ilo"
        # host извлекается из endpoint_url
        assert captured["host"] == "bmc.test"

    async def test_get_bmc_legacy_creds_without_kind(self, monkeypatch):
        """Старые creds без поля kind → передаём None, без падения."""
        from src.tasks import _bmc_helpers

        captured: dict = {}

        async def fake_get_bmc_client(**kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(_bmc_helpers, "get_bmc_client", fake_get_bmc_client)
        await _bmc_helpers.get_bmc({
            "endpoint_url": "https://bmc.test",
            "username": "u",
            "password": "p",
        })
        assert captured["kind"] is None
