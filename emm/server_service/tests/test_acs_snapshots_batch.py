"""Тесты batch-диспатча ACS-снимков — POST /servers/acs-snapshots/{create,restore}-batch.

Покрывает:
* create-batch — admin ставит задачи на несколько managed-серверов: 202,
  batch_id, per-server dispatched;
* create-batch — право `acs_snapshot` тип-wide (list/create/restore одним
  action'ом): без гранта у оператора весь батч падает 403 до цикла, не
  per-server;
* create-batch — VMS-hub сервер в списке → per-server `server_is_vms_hub`,
  остальные dispatched;
* create-batch — неподготовленный сервер → `prepare_required`;
* create-batch — ACS выключен платформенно → первый failed `acs_disabled`,
  остаток `not_attempted` (глобальный абор);
* create-batch — отдел без opt-in → per-server `acs_department_not_enabled`
  (не глобальный абор — это per-department, не platform-wide гейт);
* restore-batch — `acs_snapshot` тип-wide: без гранта у оператора
  весь батч падает 403 до цикла (не per-server);
* restore-batch — admin с bootstrap-паролем на всех серверах → dispatched;
* restore-batch — отсутствующий bootstrap-пароль версии → per-server
  `bootstrap_password_missing`;
* дубли server_id → 422; размер батча > cap → 413.
"""

from __future__ import annotations

import pytest

from src.core.constants import ServerStatus
from src.utils.ids import _new_id
from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/servers"


async def _enable_acs(db, *, department_id: str = "dep_a", enabled: bool = True) -> None:
    from src.models.acs_department_access import AcsDepartmentAccess
    from src.models.acs_settings import SINGLETON_ID, AcsSettings
    from src.services import secrets_service
    from src.utils.ids import acs_department_access_id

    row = await db.get(AcsSettings, SINGLETON_ID)
    if row is None:
        row = AcsSettings(
            id=SINGLETON_ID, enabled=enabled, acs_url="http://acs.example.com",
            acs_password_encrypted=secrets_service.encrypt(
                "acs-plaintext-secret", aad=secrets_service.aad_for_acs_password(SINGLETON_ID),
            ),
        )
        db.add(row)
    else:
        row.enabled = enabled
    dept_row = AcsDepartmentAccess(
        id=acs_department_access_id(), department_id=department_id, is_enabled=True,
    )
    db.add(dept_row)
    await db.flush()


async def _make_managed(make_server, db, *, dept="dep_a", is_vms_hub=False):
    srv = await make_server(department_id=dept)
    srv.is_managed = True
    srv.management_user = "dbos"
    srv.is_vms_hub = is_vms_hub
    await db.flush()
    return srv


async def _make_os_version(db, *, name: str) -> "object":
    from src.models import OsVersion

    osv = OsVersion(id=_new_id("osv_"), name=name, repositories=[])
    db.add(osv)
    await db.flush()
    return osv


async def _set_bootstrap_password(db, os_version_id: str) -> None:
    from src.services import os_version_bootstrap_password as bootstrap_svc

    await bootstrap_svc.upsert_bootstrap_password(
        db, os_version_id, "root", "BootstrapPwd!123", None,
    )


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват `dispatch_task_with_hit` — ACS-dispatch зовёт его с `max_attempts`."""
    calls: list[dict] = []

    async def fake_dispatch_with_hit(
        *, db, task_kind, target_server_id, payload, created_by,
        request_id, target_resource_id=None, idempotency_key=None,
        priority=0, max_attempts=3,
    ):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
        })
        return f"tsk_{len(calls)}", False

    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


class TestAcsCreateBatch:
    async def test_admin_dispatches_all(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        s1 = await _make_managed(make_server, db)
        s2 = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel batch")
        await _enable_acs(db)

        resp = await client.post(
            f"{BASE}/acs-snapshots/create-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [s1.id, s2.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["batch_id"].startswith("bat_")
        assert body["failed"] == []
        assert {d["server_id"] for d in body["dispatched"]} == {s1.id, s2.id}
        assert len(captured_dispatch) == 2
        for call in captured_dispatch:
            assert call["task_kind"] == "acs.snapshot_create"

    async def test_type_wide_permission_blocks_whole_batch(
        self, client, operator_token_a, make_server, db, captured_dispatch,
    ):
        # operator не получает acs_snapshot системным сидом (только admin) —
        # право проверяется ОДИН раз до цикла, весь батч падает 403, а не
        # per-server failed (симметрично restore-batch).
        s1 = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel perm")
        await _enable_acs(db)

        resp = await client.post(
            f"{BASE}/acs-snapshots/create-batch",
            headers=_hdr(operator_token_a),
            json={"server_ids": [s1.id], "os_version_id": osv.id},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []

    async def test_vms_hub_fails_per_server(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        ok = await _make_managed(make_server, db)
        hub = await _make_managed(make_server, db, is_vms_hub=True)
        osv = await _make_os_version(db, name="Astra 1.8 orel hub")
        await _enable_acs(db)

        resp = await client.post(
            f"{BASE}/acs-snapshots/create-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [ok.id, hub.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert [d["server_id"] for d in body["dispatched"]] == [ok.id]
        failed = {f["server_id"]: f["reason"] for f in body["failed"]}
        assert failed[hub.id] == "server_is_vms_hub"

    async def test_unprepared_server_fails_per_server(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")  # is_managed=False по умолчанию
        osv = await _make_os_version(db, name="Astra 1.8 orel unprep")
        await _enable_acs(db)

        resp = await client.post(
            f"{BASE}/acs-snapshots/create-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [srv.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == []
        assert body["failed"][0]["reason"] == "prepare_required"

    async def test_acs_disabled_aborts_remaining(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        s1 = await _make_managed(make_server, db)
        s2 = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel disabled")
        await _enable_acs(db, enabled=False)

        resp = await client.post(
            f"{BASE}/acs-snapshots/create-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [s1.id, s2.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == []
        reasons = {f["server_id"]: f["reason"] for f in body["failed"]}
        assert reasons[s1.id] == "acs_disabled"
        assert reasons[s2.id] == "not_attempted"
        assert captured_dispatch == []

    async def test_department_not_enabled_is_per_server_not_global(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        # AcsSettings включён платформенно, но per-department opt-in не выдан —
        # это НЕ platform kill-switch, поэтому не абортит остальной батч.
        from src.models.acs_settings import SINGLETON_ID, AcsSettings
        from src.services import secrets_service

        db.add(AcsSettings(
            id=SINGLETON_ID, enabled=True, acs_url="http://acs.example.com",
            acs_password_encrypted=secrets_service.encrypt(
                "pwd", aad=secrets_service.aad_for_acs_password(SINGLETON_ID),
            ),
        ))
        await db.flush()

        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel deptoff")

        resp = await client.post(
            f"{BASE}/acs-snapshots/create-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [srv.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["failed"][0]["reason"] == "acs_department_not_enabled"

    async def test_duplicate_server_id_422(
        self, client, admin_role_token_a, make_server, db,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel dup")
        resp = await client.post(
            f"{BASE}/acs-snapshots/create-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [srv.id, srv.id], "os_version_id": osv.id},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_cap_exceeded_413(
        self, client, admin_role_token_a, make_server, db, monkeypatch,
    ):
        from src.core import config as config_mod
        settings = config_mod.get_settings()
        monkeypatch.setattr(settings, "bulk_prepare_max_servers", 1)

        s1 = await _make_managed(make_server, db)
        s2 = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel cap")
        resp = await client.post(
            f"{BASE}/acs-snapshots/create-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [s1.id, s2.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 413, resp.text
        assert resp.json()["error_code"] == "ACS_SNAPSHOT_BATCH_TOO_LARGE"


class TestAcsRestoreBatch:
    async def test_admin_dispatches_all(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        s1 = await _make_managed(make_server, db)
        s2 = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel restore-batch")
        await _enable_acs(db)
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/acs-snapshots/restore-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [s1.id, s2.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert {d["server_id"] for d in body["dispatched"]} == {s1.id, s2.id}
        for call in captured_dispatch:
            assert call["task_kind"] == "acs.snapshot_restore"

    async def test_type_wide_permission_blocks_whole_batch(
        self, client, operator_token_a, make_server, db, captured_dispatch,
    ):
        # acs_snapshot_restore — тип-wide, проверяется ОДИН раз до цикла:
        # без гранта весь batch падает 403, а не per-server failed.
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel restore-perm")
        await _enable_acs(db)
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/acs-snapshots/restore-batch",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "os_version_id": osv.id},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []

    async def test_missing_bootstrap_password_per_server(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="Astra 1.8 orel no-bootstrap")
        await _enable_acs(db)
        # bootstrap-пароль НЕ задан.

        resp = await client.post(
            f"{BASE}/acs-snapshots/restore-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [srv.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["dispatched"] == []
        assert body["failed"][0]["reason"] == "bootstrap_password_missing"

    async def test_decommissioned_and_ok_mixed(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        ok = await _make_managed(make_server, db)
        dead = await _make_managed(make_server, db)
        dead.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        osv = await _make_os_version(db, name="Astra 1.8 orel mixed-restore")
        await _enable_acs(db)
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/acs-snapshots/restore-batch",
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [ok.id, dead.id], "os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert [d["server_id"] for d in body["dispatched"]] == [ok.id]
        failed = {f["server_id"]: f["reason"] for f in body["failed"]}
        assert failed[dead.id] == "decommissioned"
