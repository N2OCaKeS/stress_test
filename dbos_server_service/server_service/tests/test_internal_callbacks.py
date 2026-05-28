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
- X-Target-Department-Id strict-mode (403 на mismatch);
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


def _hdr(token: str, dept: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if dept is not None:
        headers["X-Target-Department-Id"] = dept
    return headers


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает все вызовы audit_service.emit.

    Патчит и canonical-импорт, и локальный re-import в internal_service.
    """
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    for path in (
        "src.services.internal_service.audit_service.emit",
    ):
        try:
            monkeypatch.setattr(path, fake_emit)
        except (AttributeError, ImportError):
            pass
    return captured


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
            "lspci": "00:00.0 Host bridge",
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
        # CPU-поля пишутся inline в server — повторный sync просто переписывает.
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
        assert resp.status_code == 403

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
        assert resp.status_code == 401

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
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

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
        assert resp.status_code == 422

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
            },
        )
        assert resp.status_code == 403

    async def test_controller_not_found_returns_404(
        self, client, admin_role_token_a,
    ):
        resp = await client.post(
            f"{BASE_INT}/ipmi-controllers/ipm_missing/credentials_rotated",
            headers=_hdr(admin_role_token_a),
            json={
                "new_password": "Anything1234",
                "rotated_at": _iso(),
            },
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "IPMI_CONTROLLER_NOT_FOUND"

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
            },
        )
        events = _by_action(captured_emits, "ipmi_controller.credentials_rotated_callback")
        success = [e for e in events if e.get("status") == "success"]
        assert success
        assert success[0].get("target_id") == ctrl.id


# ── X-Target-Department-Id strict mode ──────────────────────────────────────

@pytest.fixture
def strict_dept_mode(monkeypatch):
    monkeypatch.setenv("INTERNAL_REQUIRE_DEPT_HEADER", "true")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


class TestCallbackDeptHeaderStrict:
    async def test_inventory_mismatch_returns_403(
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
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "TARGET_DEPARTMENT_MISMATCH"

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
            },
        )
        assert resp.status_code == 200


# ── OpenAPI hidden — sanity-проверка для новых endpoints ────────────────────

class TestNewCallbacksHiddenFromOpenAPI:
    async def test_inventory_callback_not_in_openapi(self, client):
        resp = await client.get("/openapi.json")
        paths = resp.json().get("paths", {})
        assert not any("inventory" in p and "/internal/" in p for p in paths)
