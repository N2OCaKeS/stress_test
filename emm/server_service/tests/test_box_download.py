"""Тесты триггера скачивания бокса и приёмного статус-callback'а.

* POST /boxes/{id}/download — dispatch `box.download` (payload-shape, статус
  downloading), 400 без download_url, 409 hub не подготовлен, 404 hub чужого
  отдела, 403 guest.
* POST /internal/boxes/{id}/download-state — worker_bot пишет ready/error,
  dept-guard (нет заголовка → 403, чужой отдел → 404).
* GET /boxes/{id} отдаёт download_status.

`worker_client.dispatch_task` мочится (`make_dispatch_capture`) — реального
Redis/worker-БД нет.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from tests._helpers import assert_error, auth_hdr as _hdr, make_dispatch_capture, next_stand_number

BASE = "/api/server/v1/boxes"
INT = "/api/server/v1/internal/boxes"


@pytest_asyncio.fixture
async def make_hub(db):
    """Подготовленный VMS-hub в заданном отделе (минимальные поля)."""
    from src.models import Server
    from src.utils.ids import server_id as new_id

    async def _factory(*, department_id: str = "dep_a", is_vms_hub: bool = True) -> Server:
        suffix = uuid.uuid4().hex[:6]
        srv = Server(
            id=new_id(),
            hostname=f"hub-{suffix}",
            ip_address=f"10.30.{int(suffix[:2], 16) % 256}.{int(suffix[2:4], 16) % 256}",
            ssh_port=22,
            department_id=department_id,
            is_managed=True,
            is_vms_hub=is_vms_hub,
            virtualization=True,
            management_user="dbos",
            network_interface_name="eth0",
            number=next_stand_number(),
        )
        db.add(srv)
        await db.flush()
        return srv

    return _factory


async def _make_box(client, token, **overrides) -> str:
    body = {
        "department_id": "dep_a",
        "name": "vm_station",
        "format": "tar.gz",
        "download_url": "https://images.example.com/vm_station.tar.gz",
    }
    body.update(overrides)
    resp = await client.post(BASE, headers=_hdr(token), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ── POST /boxes/{id}/download ────────────────────────────────────────────────


class TestDownloadTrigger:
    async def test_admin_dispatches_download(
        self, client, admin_token, make_hub, monkeypatch,
    ):
        calls = make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        box_id = await _make_box(client, admin_token)

        resp = await client.post(
            f"{BASE}/{box_id}/download",
            headers=_hdr(admin_token), json={"hub_server_id": hub.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["box_id"] == box_id
        assert body["download_status"] == "downloading"
        assert body["task_id"]

        assert len(calls) == 1
        assert calls[0]["task_kind"] == "box.download"
        assert calls[0]["target_server_id"] == hub.id
        assert calls[0]["target_resource_id"] == box_id
        payload = calls[0]["payload"]
        assert payload["box_id"] == box_id
        assert payload["box_name"] == "vm_station"
        assert payload["download_url"] == "https://images.example.com/vm_station.tar.gz"
        assert payload["format"] == "tar.gz"
        assert payload["host"] == str(hub.ip_address)
        assert payload["target_department_id"] == "dep_a"

    async def test_download_marks_box_downloading(
        self, client, admin_token, make_hub, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        box_id = await _make_box(client, admin_token)

        await client.post(
            f"{BASE}/{box_id}/download",
            headers=_hdr(admin_token), json={"hub_server_id": hub.id},
        )
        got = await client.get(f"{BASE}/{box_id}", headers=_hdr(admin_token))
        assert got.json()["download_status"] == "downloading"

    async def test_no_download_url_400(
        self, client, admin_token, make_hub, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        box_id = await _make_box(client, admin_token, name="nourl", download_url=None)

        resp = await client.post(
            f"{BASE}/{box_id}/download",
            headers=_hdr(admin_token), json={"hub_server_id": hub.id},
        )
        assert_error(resp, 400, "BOX_NO_DOWNLOAD_URL")

    async def test_hub_not_prepared_409(
        self, client, admin_token, make_hub, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub(is_vms_hub=False)
        box_id = await _make_box(client, admin_token, name="hub-np")

        resp = await client.post(
            f"{BASE}/{box_id}/download",
            headers=_hdr(admin_token), json={"hub_server_id": hub.id},
        )
        assert_error(resp, 409, "HUB_NOT_PREPARED")

    async def test_hub_cross_dept_404(
        self, client, admin_token, make_hub, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub(department_id="dep_b")
        box_id = await _make_box(client, admin_token, name="hub-xd")

        resp = await client.post(
            f"{BASE}/{box_id}/download",
            headers=_hdr(admin_token), json={"hub_server_id": hub.id},
        )
        assert_error(resp, 404, "HUB_NOT_FOUND")

    async def test_guest_cannot_download(
        self, client, admin_token, guest_token_a, make_hub, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        box_id = await _make_box(client, admin_token)

        resp = await client.post(
            f"{BASE}/{box_id}/download",
            headers=_hdr(guest_token_a), json={"hub_server_id": hub.id},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_unknown_box_404(
        self, client, admin_token, make_hub, monkeypatch,
    ):
        make_dispatch_capture(monkeypatch)
        hub = await make_hub()
        resp = await client.post(
            f"{BASE}/box_missing/download",
            headers=_hdr(admin_token), json={"hub_server_id": hub.id},
        )
        assert_error(resp, 404, "BOX_NOT_FOUND")


# ── POST /internal/boxes/{id}/download-state ─────────────────────────────────


class TestDownloadStateCallback:
    async def test_worker_bot_marks_ready(
        self, client, admin_token, worker_bot_token_a,
    ):
        box_id = await _make_box(client, admin_token, name="cb-ready")
        resp = await client.post(
            f"{INT}/{box_id}/download-state",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json={"status": "ready"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["download_status"] == "ready"

        got = await client.get(f"{BASE}/{box_id}", headers=_hdr(admin_token))
        assert got.json()["download_status"] == "ready"
        assert got.json()["download_last_error"] is None

    async def test_worker_bot_marks_error(
        self, client, admin_token, worker_bot_token_a,
    ):
        box_id = await _make_box(client, admin_token, name="cb-error")
        resp = await client.post(
            f"{INT}/{box_id}/download-state",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json={"status": "error", "error": "BOX_DOWNLOAD_FETCH_FAILED"},
        )
        assert resp.status_code == 200, resp.text

        got = await client.get(f"{BASE}/{box_id}", headers=_hdr(admin_token))
        assert got.json()["download_status"] == "error"
        assert got.json()["download_last_error"] == "BOX_DOWNLOAD_FETCH_FAILED"

    async def test_missing_dept_header_403(
        self, client, admin_token, worker_bot_token_a,
    ):
        box_id = await _make_box(client, admin_token, name="cb-nohdr")
        resp = await client.post(
            f"{INT}/{box_id}/download-state",
            headers=_hdr(worker_bot_token_a),  # без X-Target-Department-Id
            json={"status": "ready"},
        )
        assert_error(resp, 403, "TARGET_DEPARTMENT_HEADER_REQUIRED")

    async def test_cross_dept_header_404(
        self, client, admin_token, worker_bot_token_a,
    ):
        box_id = await _make_box(client, admin_token, name="cb-xd")
        resp = await client.post(
            f"{INT}/{box_id}/download-state",
            headers=_hdr(worker_bot_token_a, dept="dep_b"),
            json={"status": "ready"},
        )
        assert_error(resp, 404, "BOX_NOT_FOUND")

    async def test_bad_status_422(
        self, client, admin_token, worker_bot_token_a,
    ):
        box_id = await _make_box(client, admin_token, name="cb-badstatus")
        resp = await client.post(
            f"{INT}/{box_id}/download-state",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json={"status": "downloading"},
        )
        assert resp.status_code == 422

    async def test_unknown_box_404(
        self, client, worker_bot_token_a,
    ):
        resp = await client.post(
            f"{INT}/box_missing/download-state",
            headers=_hdr(worker_bot_token_a, dept="dep_a"),
            json={"status": "ready"},
        )
        assert_error(resp, 404, "BOX_NOT_FOUND")
