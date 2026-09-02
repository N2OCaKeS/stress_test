"""OS whitelist + WARNING (известное имя → существующий flow; неизвестное → skip + audit).

`_resolve_or_create_os` опирается на `KNOWN_OS_PREFIXES`: имя, начинающееся
с одного из префиксов whitelist'а — попадает в каталог os_versions
(существующий поток + WARNING `os_version.create` при first-seen). Любое
другое имя — запись НЕ создаётся, эмитим WARNING `os.unknown_observed`,
inventory.sync продолжается без апдейта `server.os_version_id`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.known_os import KNOWN_OS_PREFIXES, is_known_os
from src.models import OsVersion, Server

from tests._helpers import make_emit_capture


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(
        monkeypatch,
        "src.services.internal_service.audit_service.emit",
        "src.services.server.audit_service.emit",
        "src.services.permission_service.audit_service.emit",
        "src.api.v1.endpoints.ipmi.audit_service.emit",
    )


class TestIsKnownOs:
    """Unit-проверки whitelist-сравнения."""

    def test_astra_se_with_version_matches(self):
        assert is_known_os("Astra Linux SE 1.7")
        assert is_known_os("Astra Linux SE 1.8.5")
        assert is_known_os("Astra Linux CE 2.12")

    def test_ubuntu_with_version_matches(self):
        assert is_known_os("Ubuntu 22.04")
        assert is_known_os("Ubuntu 24.04.1 LTS")

    def test_rhel_family_matches(self):
        assert is_known_os("Red Hat Enterprise Linux 9.4")
        assert is_known_os("RHEL 9")
        assert is_known_os("CentOS 7")
        assert is_known_os("AlmaLinux 9.4")
        assert is_known_os("Rocky Linux 9")

    def test_alt_linux_matches(self):
        assert is_known_os("ALT Linux 10")
        assert is_known_os("ALT Server 10")

    def test_red_os_matches(self):
        assert is_known_os("RED OS 7.3")

    def test_bare_version_matches(self):
        # Новый контракт: воркер шлёт чистую версию без distro-имени.
        assert is_known_os("1.8.1.6")
        assert is_known_os("1.7.5")
        assert is_known_os("1.7")

    def test_bare_integer_without_dot_rejected(self):
        # Голое число без разделителя — не версия, запись не создаём.
        assert not is_known_os("42")
        assert not is_known_os("2022")

    def test_unknown_strings_rejected(self):
        # Не префиксы whitelist'а
        assert not is_known_os("UniqueDistro 42")
        assert not is_known_os("Astra")  # без "Linux"
        assert not is_known_os("FreeBSD 14")
        assert not is_known_os("Windows Server 2022")
        assert not is_known_os("x")

    def test_none_and_empty_rejected(self):
        assert not is_known_os(None)
        assert not is_known_os("")

    def test_case_sensitive(self):
        # Worker должен отдавать ровно строку, которую видит оператор —
        # сравнение case-sensitive.
        assert not is_known_os("astra linux se 1.7")
        assert not is_known_os("UBUNTU 22.04")

    def test_all_prefixes_validate_themselves(self):
        for prefix in KNOWN_OS_PREFIXES:
            assert is_known_os(prefix), f"prefix itself should match: {prefix!r}"


# ── integration: known OS → создаётся version-row ───────────────────────────

@pytest.mark.usefixtures("soft_dept_mode")
class TestWhitelistAcceptsKnownOs:
    async def test_known_os_creates_version_row_and_links_server(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_emits,
    ):
        """Astra Linux SE 1.8.5 — known prefix → запись в каталоге, server.os_version_id выставляется."""
        srv = await make_server(department_id=dept_a)
        BASE_INT = "/api/server/v1/internal"

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={
                "hostname": f"host-{srv.id[-4:]}", "kernel": "5.15.0",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                "os_version": "Astra Linux SE 1.8.5", "disks": [],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["os_version_id"] is not None

        # Запись действительно создана и сервер на неё указывает.
        await db.commit()
        osv = (await db.execute(
            select(OsVersion).where(OsVersion.id == body["os_version_id"])
        )).scalar_one()
        assert osv.name == "Astra Linux SE 1.8.5"

        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.os_version_id == body["os_version_id"]

        # WARNING-аудит `os_version.create` эмитен (auto_from_inventory).
        creates = [
            e for e in captured_emits
            if e.get("action") == "os_version.create"
        ]
        assert len(creates) == 1
        # `os.unknown_observed` НЕ должен эмититься для known prefix'а.
        unknowns = [
            e for e in captured_emits
            if e.get("action") == "os.unknown_observed"
        ]
        assert unknowns == []


# ── integration: unknown OS → НЕ создаётся, audit WARNING, sync продолжается ─

@pytest.mark.usefixtures("soft_dept_mode")
class TestWhitelistRejectsUnknownOs:
    async def test_unknown_os_not_created_and_warning_emitted(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_emits,
    ):
        """Имя вне whitelist'а: запись в os_versions НЕ создаётся,
        эмитится WARNING `os.unknown_observed`, остальные hardware-поля
        обновляются (inventory.sync продолжается)."""
        srv = await make_server(department_id=dept_a)
        BASE_INT = "/api/server/v1/internal"

        # Серверу пока никакой OS не выставлено.
        assert srv.os_version_id is None

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={
                "hostname": "renamed-by-inv", "kernel": "5.10.0",
                "cpu_brand": "Intel", "cpu_model": "Xeon",
                "cpu_cores": 8, "cpu_threads": 16, "cpu_frequency_ghz": 2.4,
                "os_version": "RandomDistro 99.99", "disks": [],
            },
        )
        # 200 — inventory.sync прошёл, hardware-поля переписали.
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # os_version_id остался None — запись не создавалась.
        assert body["os_version_id"] is None

        await db.commit()
        # CPU/hostname поля апдейтнулись, os_version_id остался прежним (None).
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.hostname == "renamed-by-inv"
        assert refreshed.cpu_brand == "Intel"
        assert refreshed.os_version_id is None

        # Запись в os_versions с unknown-именем отсутствует.
        rows = (await db.execute(
            select(OsVersion).where(OsVersion.name == "RandomDistro 99.99")
        )).scalars().all()
        assert rows == []

        # WARNING-аудит `os.unknown_observed` эмитен.
        warns = [
            e for e in captured_emits
            if e.get("action") == "os.unknown_observed"
        ]
        assert len(warns) == 1
        d = warns[0]["details"]
        assert d["os_name"] == "RandomDistro 99.99"
        assert d["server_id"] == srv.id
        assert d["server_department_id"] == dept_a
        assert d["reason"] == "os_not_in_whitelist"
        assert warns[0]["status"] == "warning"

        # `os_version.create` НЕ эмитится (мы не создавали запись).
        creates = [
            e for e in captured_emits
            if e.get("action") == "os_version.create"
        ]
        assert creates == []

    async def test_unknown_os_does_not_overwrite_existing_link(
        self, client, worker_bot_token_a, make_server, db, dept_a, captured_emits,
    ):
        """Если у сервера уже стоит os_version_id, unknown-имя из inventory
        НЕ сбрасывает его в NULL. Случайный мусор worker'а не должен ронять
        легитимную привязку, выставленную operator'ом."""
        srv = await make_server(department_id=dept_a)
        BASE_INT = "/api/server/v1/internal"

        # Сначала валидным known-именем выставляем os_version_id.
        r1 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={
                "hostname": "h1", "kernel": "5.15.0",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                "os_version": "Ubuntu 22.04", "disks": [],
            },
        )
        assert r1.status_code == 200
        legit_os_id = r1.json()["os_version_id"]
        assert legit_os_id is not None

        # Теперь unknown — должно остаться прежнее значение.
        r2 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers={"Authorization": "Bearer " + worker_bot_token_a},
            json={
                "hostname": "h2", "kernel": "5.15.0",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                "os_version": "SomethingWeird 1.0", "disks": [],
            },
        )
        assert r2.status_code == 200
        assert r2.json()["os_version_id"] is None

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        # os_version_id не сбросился — остался legit.
        assert refreshed.os_version_id == legit_os_id
        # Hostname обновился, sync продолжился.
        assert refreshed.hostname == "h2"

        warns = [
            e for e in captured_emits
            if e.get("action") == "os.unknown_observed"
        ]
        assert len(warns) == 1
        assert warns[0]["details"]["os_name"] == "SomethingWeird 1.0"
