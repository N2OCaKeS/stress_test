"""Интеграционные тесты `/api/server/v1/internal/*` callback-endpoints.

Покрывает write-direction endpoints, которые зовёт server_worker после
выполнения dispatched-задач:

* `POST /internal/servers/{id}/inventory` — facts от inventory.sync;
* `POST /internal/ipmi-controllers/{id}/credentials_rotated` — после worker-side
  encrypt'а нового IPMI-пароля.

Проверяется:
- happy path с upsert'ом os_versions / server_disks и inline CPU-полей в server;
- 403 для не-worker_bot / reader / без token'а;
- 404 для несуществующих server_id / controller_id;
- X-Target-Department-Id scoping (403 на missing, 404 на mismatch);
- audit-emission для обоих событий;
- worker_bot имеет grant'ы; admin-роль тоже работает; reader — нет.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.models import OsVersion, Server, ServerDisk
from src.services import secrets_service

BASE_INT = "/api/server/v1/internal"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает все вызовы audit_service.emit.

    Патчит и canonical-импорт, и локальный re-import в internal_service.
    """
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.services.internal_service.audit_service.emit",
    )


def _by_action(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


# ── inventory callback ──────────────────────────────────────────────────────

@pytest.mark.usefixtures("soft_dept_mode")
class TestInventoryCallback:
    async def test_admin_inventory_upserts_cpu_os_disks(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        payload = {
            "hostname": "srv-renamed",
            "kernel": "5.10.0-astra-amd64",
            "cpu_brand": "Intel",
            "cpu_model": "Intel Xeon E5-2690",
            "cpu_cores": 16,
            "cpu_threads": 32,
            "cpu_frequency_ghz": 2.9,
            "os_version": "Astra Linux SE 1.7",
            "disks": [
                {"name": "sda", "size_gb": 500, "model": "ST500", "is_system": True},
                {"name": "sdb", "size_gb": 1000, "model": "WD1T"},
            ],
        }
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["disks_upserted"] == 2
        assert body["os_version_id"]
        # Каталога cpu_models больше нет — cpu_id в ответе тоже нет.
        assert "cpu_id" not in body

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.hostname == "srv-renamed"
        assert refreshed.cpu_brand == "Intel"
        assert refreshed.cpu_model == "Intel Xeon E5-2690"
        assert refreshed.cpu_cores == 16
        assert refreshed.cpu_threads == 32
        assert refreshed.cpu_frequency_ghz == 2.9
        assert refreshed.os_version_id == body["os_version_id"]
        assert refreshed.os_last_synced_at is not None

        os_ver = (await db.execute(
            select(OsVersion).where(OsVersion.id == body["os_version_id"])
        )).scalar_one()
        assert os_ver.name == "Astra Linux SE 1.7"

        disks = (await db.execute(
            select(ServerDisk).where(ServerDisk.server_id == srv.id)
        )).scalars().all()
        assert {d.device_name for d in disks} == {"sda", "sdb"}
        sda = next(d for d in disks if d.device_name == "sda")
        assert sda.is_system is True
        assert sda.size_gb == 500

    async def test_inventory_second_call_updates_existing_disks(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        first = {
            "hostname": "first-host",
            "kernel": "5.10.0",
            "cpu_brand": "MCST",
            "cpu_model": "Elbrus 8C",
            "cpu_cores": 8,
            "os_version": "Astra 1.6",
            "disks": [{"name": "sda", "size_gb": 100, "is_system": True}],
        }
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=first,
        )
        second = {**first, "disks": [
            {"name": "sda", "size_gb": 200, "model": "BIGGER", "is_system": True},
        ]}
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=second,
        )
        assert resp.status_code == 200
        # CPU-поля совпадают с первым sync'ом — drift не возникает, значения
        # остаются прежними (warn-on-drift трогает только расхождения).
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.cpu_brand == "MCST"
        assert refreshed.cpu_model == "Elbrus 8C"
        assert refreshed.cpu_cores == 8
        # Disk обновился, а не создался второй раз
        disks = (await db.execute(
            select(ServerDisk).where(ServerDisk.server_id == srv.id)
        )).scalars().all()
        assert len(disks) == 1
        assert disks[0].size_gb == 200
        assert disks[0].model == "BIGGER"

    async def test_cpu_drift_not_overwritten_emits_warning(
        self, client, admin_role_token_a, make_server, db, dept_a, captured_emits,
    ):
        """Warn-on-drift: cpu-факт разошёлся с БД → WARNING, БД НЕ перетёрта."""
        srv = await make_server(department_id=dept_a)
        first = {
            "hostname": "drift-host",
            "kernel": "5.10.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon Gold 6230",
            "cpu_cores": 20,
            "cpu_threads": 40,
            "cpu_frequency_ghz": 2.1,
            "os_version": "Astra Linux SE 1.7",
            "disks": [],
        }
        # First-write — поля заполняются.
        r1 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=first,
        )
        assert r1.status_code == 200, r1.text
        assert set(r1.json()["first_write_fields"]) == {
            "cpu_brand", "cpu_model", "cpu_cores", "cpu_threads", "cpu_frequency_ghz",
        }
        assert r1.json()["drift_fields"] == []

        captured_emits.clear()
        # Второй sync приносит ДРУГОЙ cpu — это drift, перетирать нельзя.
        drifted = {**first, "cpu_brand": "AMD", "cpu_model": "EPYC 7763", "cpu_cores": 64}
        r2 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=drifted,
        )
        assert r2.status_code == 200, r2.text
        assert set(r2.json()["drift_fields"]) == {"cpu_brand", "cpu_model", "cpu_cores"}
        assert r2.json()["first_write_fields"] == []

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        # Значения НЕ перетёрты — БД остаётся источником истины.
        assert refreshed.cpu_brand == "Intel"
        assert refreshed.cpu_model == "Xeon Gold 6230"
        assert refreshed.cpu_cores == 20

        drift_events = _by_action(captured_emits, "inventory.drift_detected")
        assert len(drift_events) == 1
        ev = drift_events[0]
        assert ev["status"] == "warning"
        assert set(ev["details"]["fields"]) == {"cpu_brand", "cpu_model", "cpu_cores"}
        assert ev["details"]["drift"]["cpu_brand"] == {"old": "Intel", "new": "AMD"}
        assert ev["details"]["drift"]["cpu_cores"] == {"old": 20, "new": 64}

    async def test_no_drift_when_facts_match(
        self, client, admin_role_token_a, make_server, db, dept_a, captured_emits,
    ):
        """Совпадающие факты на повторном sync'е drift не поднимают."""
        srv = await make_server(department_id=dept_a)
        payload = {
            "hostname": "same-host",
            "kernel": "5.10.0",
            "cpu_brand": "MCST",
            "cpu_model": "Elbrus 16C",
            "cpu_cores": 16,
            "cpu_threads": 16,
            "cpu_frequency_ghz": 2.0,
            "os_version": "Astra Linux SE 1.7",
            "disks": [],
        }
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=payload,
        )
        captured_emits.clear()
        r2 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=payload,
        )
        assert r2.status_code == 200
        assert r2.json()["drift_fields"] == []
        assert r2.json()["first_write_fields"] == []
        assert _by_action(captured_emits, "inventory.drift_detected") == []

    async def test_inventory_writes_os_repositories_and_security_mode(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        """Callback с чистой версией + repositories + os_security_mode: каталог
        получает версию "1.8.1.6" с репозиториями, сервер линкуется и несёт
        per-server режим "Smolensk"; read-схема сервера его отдаёт."""
        srv = await make_server(department_id=dept_a)
        repos = [
            "http://dl.astralinux.ru/astra/stable/1.7_x86-64/repository-main/",
            "http://dl.astralinux.ru/astra/stable/1.7_x86-64/repository-update/",
        ]
        payload = {
            "hostname": "smolensk-box",
            "kernel": "5.15.0-astra-amd64",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon Gold",
            "cpu_cores": 8,
            "os_version": "1.8.1.6",
            "os_security_mode": "Smolensk",
            "repositories": repos,
            "disks": [],
        }
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        os_version_id = resp.json()["os_version_id"]
        assert os_version_id

        await db.commit()
        os_ver = (await db.execute(
            select(OsVersion).where(OsVersion.id == os_version_id)
        )).scalar_one()
        # Каталог — только версия, без режима.
        assert os_ver.name == "1.8.1.6"
        assert os_ver.repositories == repos

        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.os_version_id == os_version_id
        assert refreshed.os_security_mode == "Smolensk"

        # Read-схема сервера отдаёт os_security_mode (UI склеит "1.8.1.6 Smolensk").
        get = await client.get(
            f"/api/server/v1/servers/{srv.id}", headers=_hdr(admin_role_token_a),
        )
        assert get.status_code == 200, get.text
        assert get.json()["os_security_mode"] == "Smolensk"

    async def test_inventory_repeat_overwrites_repositories(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        """Повторный inventory с новым списком репозиториев перезаписывает
        хранимый снапшот версии ОС."""
        srv = await make_server(department_id=dept_a)
        base = {
            "hostname": "box",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon",
            "cpu_cores": 4,
            "os_version": "1.8.1.6",
            "disks": [],
        }
        first = {**base, "repositories": ["http://repo.example/old/"]}
        r1 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=first,
        )
        assert r1.status_code == 200, r1.text
        osv_id = r1.json()["os_version_id"]

        new_repos = [
            "http://repo.example/main/",
            "http://repo.example/update/",
        ]
        second = {**base, "repositories": new_repos}
        r2 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=second,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["os_version_id"] == osv_id

        await db.commit()
        os_ver = (await db.execute(
            select(OsVersion).where(OsVersion.id == osv_id)
        )).scalar_one()
        assert os_ver.repositories == new_repos

    async def test_inventory_empty_repositories_does_not_wipe(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        """Повторный inventory с пустым списком репозиториев не затирает уже
        сохранённые (сбой sources.list / старый воркер не обнуляют каталог)."""
        srv = await make_server(department_id=dept_a)
        base = {
            "hostname": "box2",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon",
            "cpu_cores": 4,
            "os_version": "1.8.1.6",
            "disks": [],
        }
        stored = ["http://repo.example/kept/"]
        r1 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json={**base, "repositories": stored},
        )
        assert r1.status_code == 200, r1.text
        osv_id = r1.json()["os_version_id"]

        r2 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json={**base, "repositories": []},
        )
        assert r2.status_code == 200, r2.text

        await db.commit()
        os_ver = (await db.execute(
            select(OsVersion).where(OsVersion.id == osv_id)
        )).scalar_one()
        assert os_ver.repositories == stored

    async def test_inventory_without_repositories_field_backcompat(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        """Старый воркер без поля repositories: callback не падает, репозитории
        версии не трогаются."""
        srv = await make_server(department_id=dept_a)
        # Поле repositories вовсе отсутствует в payload'е.
        payload = {
            "hostname": "legacy-box",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon",
            "cpu_cores": 4,
            "os_version": "1.8.1.6",
            "disks": [],
        }
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        osv_id = resp.json()["os_version_id"]
        assert osv_id

        await db.commit()
        os_ver = (await db.execute(
            select(OsVersion).where(OsVersion.id == osv_id)
        )).scalar_one()
        assert os_ver.repositories == []

    async def test_inventory_os_security_mode_persist_and_backcompat(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        """Режим пишется из callback'а; повторный inventory без os_security_mode
        (старый воркер) существующее значение не затирает."""
        srv = await make_server(department_id=dept_a)
        base = {
            "hostname": "mode-box",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon",
            "cpu_cores": 4,
            "os_version": "1.8.1.6",
            "disks": [],
        }
        r1 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json={**base, "os_security_mode": "Voronezh"},
        )
        assert r1.status_code == 200, r1.text
        await db.commit()
        srv_row = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert srv_row.os_security_mode == "Voronezh"

        # Старый воркер: поля os_security_mode в payload'е нет — режим сохраняется.
        r2 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=base,
        )
        assert r2.status_code == 200, r2.text
        await db.commit()
        srv_row2 = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert srv_row2.os_security_mode == "Voronezh"

    async def test_inventory_network_memory_disk_usage_persist_and_read(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        """Callback принимает сеть/память/занятость дисков, хранит и отдаёт в
        read-схеме сервера."""
        srv = await make_server(department_id=dept_a)
        payload = {
            "hostname": "hw-box",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon",
            "cpu_cores": 8,
            "os_version": "1.8.1.6",
            "ram_total_mb": 16384,
            "network_interfaces": ["ens192", "ens224"],
            "disks": [
                {"name": "sda", "size_gb": 500, "used_gb": 225,
                 "used_percent": 48.4, "model": "SSD", "is_system": True},
                {"name": "sdb", "size_gb": 1000, "used_gb": 465,
                 "used_percent": 50.0, "is_system": False},
            ],
        }
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["disks_upserted"] == 2

        await db.commit()
        srv_row = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert srv_row.ram_total_mb == 16384
        assert srv_row.network_interfaces == ["ens192", "ens224"]
        # first-write: основной интерфейс подхватился из первого имени.
        assert srv_row.network_interface_name == "ens192"

        disks = (await db.execute(
            select(ServerDisk).where(ServerDisk.server_id == srv.id)
        )).scalars().all()
        sda = next(d for d in disks if d.device_name == "sda")
        assert sda.is_system is True
        assert sda.used_gb == 225
        assert sda.used_percent == 48.4

        # Read-схема сервера отдаёт новые поля.
        read = await client.get(
            f"/api/server/v1/servers/{srv.id}",
            headers=_hdr(admin_role_token_a),
        )
        assert read.status_code == 200, read.text
        body = read.json()
        assert body["ram_total_mb"] == 16384
        assert body["ram_total_gb"] == 16.0
        assert body["network_interfaces"] == ["ens192", "ens224"]
        sda_resp = next(d for d in body["storage"] if d["slot"] == "sda")
        assert sda_resp["is_system"] is True
        assert sda_resp["used_gb"] == 225
        assert sda_resp["used_percent"] == 48.4

    async def test_inventory_without_network_memory_backcompat(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        """Старый воркер без ram/network/used: callback не падает, поля пустые."""
        srv = await make_server(department_id=dept_a)
        payload = {
            "hostname": "legacy-hw",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon",
            "cpu_cores": 4,
            "os_version": "1.8.1.6",
            "disks": [{"name": "sda", "size_gb": 500, "is_system": False}],
        }
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a), json=payload,
        )
        assert resp.status_code == 200, resp.text

        await db.commit()
        srv_row = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert srv_row.ram_total_mb is None
        assert srv_row.network_interfaces is None

        read = await client.get(
            f"/api/server/v1/servers/{srv.id}",
            headers=_hdr(admin_role_token_a),
        )
        assert read.status_code == 200, read.text
        body = read.json()
        assert body["ram_total_gb"] is None
        assert body["network_interfaces"] == []
        assert body["storage"][0]["used_gb"] is None

    async def test_inventory_network_empty_does_not_wipe(
        self, client, admin_role_token_a, make_server, db, dept_a,
    ):
        """Повторный inventory с пустым network_interfaces не затирает сохранённый
        список (сбой ip-probe / старый воркер)."""
        srv = await make_server(department_id=dept_a)
        base = {
            "hostname": "net-box",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon",
            "cpu_cores": 4,
            "os_version": "1.8.1.6",
            "disks": [],
        }
        r1 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json={**base, "network_interfaces": ["eno1"]},
        )
        assert r1.status_code == 200, r1.text
        r2 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json={**base, "network_interfaces": []},
        )
        assert r2.status_code == 200, r2.text
        await db.commit()
        srv_row = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert srv_row.network_interfaces == ["eno1"]

    async def test_ram_drift_not_overwritten_emits_warning(
        self, client, admin_role_token_a, make_server, db, dept_a, captured_emits,
    ):
        """RAM подчиняется warn-on-drift: расхождение с БД не перетирается, летит
        WARNING inventory.drift_detected."""
        srv = await make_server(department_id=dept_a)
        base = {
            "hostname": "ram-box",
            "kernel": "5.15.0",
            "cpu_brand": "Intel",
            "cpu_model": "Xeon",
            "cpu_cores": 4,
            "os_version": "1.8.1.6",
            "disks": [],
        }
        r1 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json={**base, "ram_total_mb": 16384},
        )
        assert r1.status_code == 200, r1.text
        r2 = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json={**base, "ram_total_mb": 8192},
        )
        assert r2.status_code == 200, r2.text
        assert "ram_total_mb" in r2.json()["drift_fields"]

        await db.commit()
        srv_row = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert srv_row.ram_total_mb == 16384  # НЕ перетёрто
        drift_events = _by_action(captured_emits, "inventory.drift_detected")
        assert any("ram_total_mb" in e["details"]["fields"] for e in drift_events)

    async def test_worker_bot_can_callback_inventory(
        self, client, worker_bot_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(worker_bot_token_a),
            json={
                "hostname": "bot-host",
                "kernel": "5.10.0",
                "cpu_brand": "Intel",
                "cpu_model": "Xeon-bot",
                "cpu_cores": 4,
                "os_version": "Astra-bot",
                "disks": [],
            },
        )
        assert resp.status_code == 200, resp.text

    async def test_reader_cannot_callback_inventory(
        self, client, reader_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(reader_token_a),
            json={
                "hostname": "x", "kernel": "x",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                "os_version": "x", "disks": [],
            },
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client, make_server, dept_a):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            json={
                "hostname": "x", "kernel": "x",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                "os_version": "x", "disks": [],
            },
        )
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_server_not_found_returns_404(
        self, client, admin_role_token_a,
    ):
        resp = await client.post(
            f"{BASE_INT}/servers/srv_missing/inventory",
            headers=_hdr(admin_role_token_a),
            json={
                "hostname": "x", "kernel": "x",
                "cpu_brand": None, "cpu_model": None,
                "cpu_cores": 1, "cpu_threads": None, "cpu_frequency_ghz": None,
                "os_version": "x", "disks": [],
            },
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_invalid_payload_returns_422(
        self, client, admin_role_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            # отсутствует cpu_model и пр.
            json={"hostname": "x"},
        )
        assert_error(resp, 422)

    async def test_audit_inventory_received_emitted(
        self, client, admin_role_token_a, make_server, captured_emits, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a),
            json={
                "hostname": "h", "kernel": "k",
                "cpu_brand": "Intel", "cpu_model": "Audit-CPU",
                "cpu_cores": 2, "os_version": "AuditOS", "disks": [],
            },
        )
        events = _by_action(captured_emits, "server.inventory_received")
        success = [e for e in events if e.get("status") == "success"]
        assert success, f"expected success emit, got {events}"
        assert success[0].get("target_id") == srv.id
        # CPU-поля пишутся в audit-details (whitelist) ради инвестигативного следа.
        assert success[0]["details"].get("cpu_brand") == "Intel"
        assert success[0]["details"].get("cpu_model") == "Audit-CPU"


# ── IPMI credentials_rotated callback ───────────────────────────────────────

@pytest.mark.usefixtures("soft_dept_mode")
class TestIpmiCredentialsRotatedCallback:
    async def test_admin_records_rotation(
        self, client, admin_role_token_a, make_server, make_ipmi, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id, password="old-pwd")
        rotated_at = datetime.now(timezone.utc)
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "RotatedWorker1234",
                "rotated_at": rotated_at.isoformat(),
                "verified_at": rotated_at.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

        await db.commit()
        from src.models import IpmiController
        refreshed = (await db.execute(
            select(IpmiController).where(IpmiController.id == ctrl.id)
        )).scalar_one()
        assert refreshed.password_rotated_at is not None
        # server_service зашифровал plaintext сам — decrypt возвращает то же значение.
        assert secrets_service.decrypt(
            refreshed.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(refreshed.id),
        ) == "RotatedWorker1234"

    async def test_worker_bot_can_record_rotation(
        self, client, worker_bot_token_a, make_server, make_ipmi, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(worker_bot_token_a),
            json={
                "new_password": "BotRotated1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        assert resp.status_code == 200, resp.text

    async def test_reader_cannot_record_rotation(
        self, client, reader_token_a, make_server, make_ipmi, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(reader_token_a),
            json={
                "new_password": "ReaderTry1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_controller_not_found_returns_404(
        self, client, admin_role_token_a,
    ):
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/ipm_missing/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "Anything1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")

    async def test_audit_credentials_rotated_callback_emitted(
        self, client, admin_role_token_a, make_server, make_ipmi,
        captured_emits, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)
        await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "AuditPwd1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        events = _by_action(captured_emits, "ipmi_controller.credentials_rotated_callback")
        success = [e for e in events if e.get("status") == "success"]
        assert success
        assert success[0].get("target_id") == ctrl.id
        # verified_at должен попасть в audit details (verify-then-storage trace).
        assert success[0]["details"].get("verified_at") is not None

    async def test_missing_verified_at_returns_422(
        self, client, admin_role_token_a, make_server, make_ipmi, dept_a,
    ):
        """Без `verified_at` Pydantic отбивает 422 — поле обязательное."""
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "NoVerify1234",
                "rotated_at": _iso(),
            },
        )
        assert_error(resp, 422)

    async def test_stale_verified_at_rejected_400(
        self, client, admin_role_token_a, make_server, make_ipmi, db, dept_a,
    ):
        """`verified_at` старше IPMI_VERIFY_MAX_AGE_SECONDS → 400 BMC_VERIFY_REQUIRED.

        Storage не должен обновиться — старый ciphertext остаётся, чтобы
        BMC доступ не потерялся при разорванном verify→storage пути.
        """
        from datetime import timedelta
        from src.models import IpmiController

        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id, password="old-pwd-stable")
        # 1 час назад — за пределами 60s окна.
        stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "StaleVerify1234",
                "rotated_at": _iso(),
                "verified_at": stale,
            },
        )
        body = assert_error(resp, 400, "BMC_VERIFY_REQUIRED")
        # Message в обычном stale-случае без future-формулировки.
        assert "future" not in body["message"].lower()

        # Storage не должен мутироваться.
        await db.commit()
        refreshed = (await db.execute(
            select(IpmiController).where(IpmiController.id == ctrl.id)
        )).scalar_one()
        # password_rotated_at не выставился (если был None — остаётся None).
        # И новый пароль не сохранён — decrypt старого должен вернуть исходный.
        assert secrets_service.decrypt(
            refreshed.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(refreshed.id),
        ) == "old-pwd-stable"

    async def test_future_verified_at_rejected_with_future_message(
        self, client, admin_role_token_a, make_server, make_ipmi, dept_a,
    ):
        """`verified_at` в будущем → отдельный message «in the future».

        Часы worker'а могут уехать вперёд, либо это сознательный spoof.
        В обоих случаях верить proof'у нельзя; reply должен отличаться от
        обычного stale-случая, чтобы оператор в logs сразу видел причину.
        """
        from datetime import timedelta

        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id, password="old-pwd-stable")
        # 1 час вперёд — за пределами 60s окна с другой стороны.
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "FutureVerify1234",
                "rotated_at": _iso(),
                "verified_at": future,
            },
        )
        body = assert_error(resp, 400, "BMC_VERIFY_REQUIRED")
        assert "future" in body["message"].lower()

    async def test_rotated_at_far_future_rejected_400(
        self, client, admin_role_token_a, make_server, make_ipmi, db, dept_a,
    ):
        """`rotated_at` сильно в будущем — 400 ROTATED_AT_IN_FUTURE, storage не трогаем.

        Worker с убежавшими часами или подменённый PAT мог бы прислать «ротация
        через год» и навсегда вытолкнуть запись в топ списка по
        `password_rotated_at`. Допускаем ±10 минут на NTP-skew; больше — отбой.
        """
        from datetime import timedelta
        from src.models import IpmiController

        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id, password="old-stable")
        # 1 год вперёд — далеко за пределами skew 600s.
        far_future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "FutureRotated1234",
                "rotated_at": far_future,
                "verified_at": _iso(),
            },
        )
        assert_error(resp, 400, "ROTATED_AT_IN_FUTURE")

        # Storage не мутируется — старый ciphertext на месте.
        await db.commit()
        refreshed = (await db.execute(
            select(IpmiController).where(IpmiController.id == ctrl.id)
        )).scalar_one()
        assert secrets_service.decrypt(
            refreshed.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(refreshed.id),
        ) == "old-stable"

    async def test_rotated_at_far_past_rejected_400(
        self, client, admin_role_token_a, make_server, make_ipmi, db, dept_a,
    ):
        """`rotated_at` далеко в прошлом — 400 ROTATED_AT_TOO_OLD, storage не трогаем.

        Симметрично верхней границе. Worker с очень отставшими часами либо
        умышленный spoof мог бы утопить новую запись в конце списка по
        `password_rotated_at`. Допускаем ±10 минут — больше отбой.
        """
        from datetime import timedelta
        from src.models import IpmiController

        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id, password="old-stable")
        far_past = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "PastRotated1234",
                "rotated_at": far_past,
                "verified_at": _iso(),
            },
        )
        assert_error(resp, 400, "ROTATED_AT_TOO_OLD")

        await db.commit()
        refreshed = (await db.execute(
            select(IpmiController).where(IpmiController.id == ctrl.id)
        )).scalar_one()
        assert secrets_service.decrypt(
            refreshed.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(refreshed.id),
        ) == "old-stable"

    async def test_rotated_at_small_skew_accepted(
        self, client, admin_role_token_a, make_server, make_ipmi, dept_a,
    ):
        """Малый NTP-перекос (≤10 минут) принимаем — иначе типовой clock-drift валит prod."""
        from datetime import timedelta

        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id, password="ok")
        # 2 минуты вперёд — внутри skew.
        small_future = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "SmallSkewOk1234",
                "rotated_at": small_future,
                "verified_at": _iso(),
            },
        )
        assert resp.status_code == 200, resp.text

    async def test_repeat_callback_returns_409_credentials_already_applied(
        self, client, admin_role_token_a, make_server, make_ipmi, db, dept_a,
    ):
        """Повторный callback на уже не-pending row → 409 CREDENTIALS_ALREADY_APPLIED.

        Контракт `API_ENDPOINTS.md` и `internal.py:305` обещают этот код для
        ретрая worker'а после успешного первого callback'а. Сторадж не должен
        мутироваться: ciphertext остаётся тот, что положил первый callback.
        """
        from src.models import IpmiController

        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id, password="old-pwd")
        first = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "FirstApply1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "SecondApply9999",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        assert_error(second, 409, "CREDENTIALS_ALREADY_APPLIED")

        await db.commit()
        refreshed = (await db.execute(
            select(IpmiController).where(IpmiController.id == ctrl.id)
        )).scalar_one()
        # Storage остался от первого callback'а.
        assert secrets_service.decrypt(
            refreshed.password_encrypted,
            aad=secrets_service.aad_for_ipmi_credential(refreshed.id),
        ) == "FirstApply1234"
        assert refreshed.credentials_pending_apply is False

    async def test_callback_on_not_pending_controller_returns_409(
        self, client, admin_role_token_a, make_server, make_ipmi, dept_a,
    ):
        """Свежеcозданный controller без pending_apply → 409 на первом же callback'е.

        Сценарий «worker дёрнул endpoint, не выставив pending через dispatch».
        Без enforcement'а row тихо переписалась бы — теперь отбиваем 409.
        """
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(
            server_id=srv.id,
            password="kept",
            credentials_pending_apply=False,
        )
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "WouldOverwrite1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        assert_error(resp, 409, "CREDENTIALS_ALREADY_APPLIED")

    async def test_credentials_already_applied_emits_failure_audit(
        self, client, admin_role_token_a, make_server, make_ipmi,
        captured_emits, dept_a,
    ):
        """409 CREDENTIALS_ALREADY_APPLIED ложится в audit как failure."""
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(
            server_id=srv.id, credentials_pending_apply=False,
        )
        await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "NoApply1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        events = _by_action(captured_emits, "ipmi_controller.credentials_rotated_callback")
        failures = [
            e for e in events
            if e.get("status") == "failure"
            and e["details"].get("reason") == "credentials_already_applied"
        ]
        assert failures, f"expected credentials_already_applied failure, got {events}"
        assert failures[0].get("target_id") == ctrl.id


# ── X-Target-Department-Id strict mode ──────────────────────────────────────

@pytest.fixture
def strict_dept_mode(monkeypatch):
    monkeypatch.setenv("INTERNAL_REQUIRE_DEPT_HEADER", "true")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


class TestCallbackDeptHeaderStrict:
    async def test_inventory_mismatch_returns_404(
        self, client, admin_role_token_a, make_server, strict_dept_mode, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(admin_role_token_a, dept="dep_b"),
            json={
                "hostname": "x", "kernel": "x",
                "cpu_brand": "Intel", "cpu_model": "Strict-CPU",
                "cpu_cores": 1, "os_version": "x", "disks": [],
            },
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_ipmi_callback_matched_header_returns_200(
        self, client, admin_role_token_a, make_server, make_ipmi,
        strict_dept_mode, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(admin_role_token_a, dept=dept_a),
            json={
                "new_password": "StrictOk1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        assert resp.status_code == 200


# ── OpenAPI hidden — sanity-проверка для новых endpoints ────────────────────

class TestNewCallbacksHiddenFromOpenAPI:
    async def test_inventory_callback_not_in_openapi(self, client):
        resp = await client.get("/openapi.json")
        paths = resp.json().get("paths", {})
        assert not any("inventory" in p and "/internal/" in p for p in paths)


# ── Enum-oracle: header dept mismatch masked as 404 ─────────────────────────
#
# Write-direction callbacks не должны разделять «не существует» и «существует
# в чужом dept» — иначе compromised worker_bot проброcом id'шников по разнице
# 403 vs 404 enum'ит cross-dept данные. Заголовок ≠ server.department_id →
# 404-маска; отсутствие заголовка → 403 (single guard); missing-permission
# отбивается раньше dept-check'а собственным 403.


class TestInternalCallbacksDeptMaskedAs404:
    async def test_inventory_wrong_header_returns_404(
        self, client, make_token, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        foreign_token = make_token(
            department_id="dep_b",
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/inventory",
            headers=_hdr(foreign_token, dept="dep_b"),  # ≠ server dep_a
            json={
                "hostname": "x", "kernel": "x",
                "cpu_brand": "Intel", "cpu_model": "Foreign",
                "cpu_cores": 1, "os_version": "x", "disks": [],
            },
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_users_inventory_no_header_returns_403(
        self, client, make_token, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        foreign_token = make_token(
            department_id="dep_b",
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/users/inventory",
            headers=_hdr(foreign_token),
            json={"users": []},
        )
        assert_error(resp, 403)

    async def test_provision_status_bad_payload_returns_422_contract(
        self, client, make_token, make_server, make_account, dept_a,
    ):
        """provision_status: payload-validation срабатывает ДО dept-guard.

        Эндпоинт ожидает enum-`operation` (`useradd` не в whitelist'е
        FastAPI-схемы) и отбивает 422 на схеме раньше, чем доходит до
        dept-check'а. Контракт: 422 здесь — норма, потому что schema-валидация
        всегда выполняется первой и enumerable-leak невозможен (ответ одинаков
        и для owner'а, и для чужого dept'а). Поведение зафиксировано тестом,
        чтобы случайный merge guard'а перед валидацией не превратил его в leak.
        """
        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, password="leak-target")
        foreign_token = make_token(
            department_id="dep_b",
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/provision_status",
            headers=_hdr(foreign_token, dept="dep_b"),
            json={"operation": "useradd", "present": True},
        )
        assert_error(resp, 422)

    async def test_prepared_no_permission_returns_403_permission_denied(
        self, client, make_token, make_server, dept_a,
    ):
        """`prepare_callback` — worker_bot-only грант; admin его не имеет.

        Admin без grant'а получает 403 PERMISSION_DENIED ещё ДО dept-check'а:
        permission проверяется первой, симметрично остальным internal-функциям
        (`fetch_account_password`, `record_provision_status`).
        """
        srv = await make_server(department_id=dept_a)
        foreign_token = make_token(
            department_id="dep_b",
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(foreign_token, dept="dep_a"),
            json={"management_user": "ops"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_credentials_rotated_wrong_header_returns_404(
        self, client, make_token, make_server, make_ipmi, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)
        foreign_token = make_token(
            department_id="dep_b",
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers=_hdr(foreign_token, dept="dep_b"),  # ≠ server dep_a
            json={
                "new_password": "ForeignTry1234",
                "rotated_at": _iso(),
                "verified_at": _iso(),
            },
        )
        body = assert_error(resp, 404, "NO_IPMI_CONTROLLER")
        # Controller-scoped endpoint — details несут `target_id` = controller_id.
        details = body.get("details") or {}
        assert details.get("target_id") == ctrl.id
        assert "server_id" not in details
        assert "controller_id" not in details


# ── target_id единый ключ в details (без mislabel) ─────────────────────────


class TestCheckTargetDeptFieldLabel:
    """`_check_target_department` пишет `details["target_id"]=<target_id>`.

    Тип target'а различается через `target_type` (отдельный аудит-поле),
    не по имени ключа в details. Старый дефолт `server_id` для account/
    controller caller'ов слал SIEM `acc_*`/`ipm_*` под именем `server_id`.
    """

    async def test_account_password_header_required_uses_target_id_key(
        self, client, worker_pat_token, make_server, make_account, strict_dept_mode,
    ):
        """Strict-mode: missing header → `details["target_id"] == account_id`."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="leak-target")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password",
            headers=_hdr(worker_pat_token),
        )
        body = assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")
        details = body.get("details") or {}
        assert details.get("target_id") == acc.id
        assert "server_id" not in details
        assert "account_id" not in details

    async def test_account_rotate_mismatch_uses_target_id_key(
        self, client, worker_pat_token, make_server, make_account, strict_dept_mode,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old")
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/accounts/{acc.id}/password/rotate",
            headers={
                "Authorization": f"Bearer {worker_pat_token}",
                "X-Target-Department-Id": "dep_b",
            },
            json={"password": "NewLabeled1234"},
        )
        body = assert_error(resp, 404, "ACCOUNT_NOT_FOUND")
        details = body.get("details") or {}
        assert details.get("target_id") == acc.id
        assert "server_id" not in details
        assert "account_id" not in details

    async def test_ipmi_credentials_uses_target_id_key(
        self, client, worker_pat_token, make_server, make_ipmi, strict_dept_mode,
    ):
        """`fetch_ipmi_credentials` target_id=server_id — едет под единым
        ключом `target_id`."""
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="ipmi-strict")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(worker_pat_token),
        )
        body = assert_error(resp, 403)
        details = body.get("details") or {}
        assert details.get("target_id") == srv.id
        assert "server_id" not in details


# ── strict_dept_mode reused in other modules' tests; ensure fixture access ──
# (fixture определена выше в этом же файле; pytest её разрешит)
