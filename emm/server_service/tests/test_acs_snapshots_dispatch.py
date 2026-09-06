"""Тесты busy_note-формата и capture/restore прежней брони вокруг ACS.

Покрывает фундамент задачи «отдельный ACS-статус занятости»:

* формат busy_note при dispatch — `ACS_CREATE_<rc>` / `ACS_RESTORE_<rc>`
  (без старого человекочитаемого текста и без пробелов);
* `pre_acs_busy_snapshot` — capture текущей брони перед выставлением
  `busy_state=acs` (single create/restore dispatch);
* dispatch упал (idempotent conflict / worker недоступен) — бронь
  восстанавливается из снимка, а не сбрасывается в `free` безусловно;
* callback `acs-snapshot-created` (create) — восстанавливает прежнюю бронь
  в любом исходе (succeeded True/False);
* callback `acs-snapshot-restore-done` — `succeeded=False` восстанавливает
  прежнюю бронь; `succeeded=True` держит `busy_state=acs`, но переводит
  `busy_note` на `ACS_RESTORE_PREPARE_<rc>` (или
  `ACS_RESTORE_BOOTSTRAP_MISSING_<rc>`, если пароль версии пропал);
* callback `prepared` — если сервер всё ещё `acs` (авто-prepare после
  restore), восстанавливает бронь, снятую в начале всей цепочки
  create/restore, вместо жёсткого `free`;
* unit-тесты чистых функций `reservation.capture_pre_acs_state` /
  `restore_pre_acs_state` (fallback на free при отсутствующем/битом снимке)
  и `ensure_not_acs_locked` (admin проходит, остальные — 409 SERVER_ACS_BUSY).

Реальный PostgreSQL через сервисный docker-compose.test.yml (см. conftest) —
кроме unit-класса, который работает с ORM-объектом в памяти без flush/commit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import IPv4Address

import pytest
from sqlalchemy import select

from src.core.constants import BusyState, PlatformRole
from src.core.exceptions import ConflictError, ServiceUnavailableError
from src.models import Server
from src.schemas.identity import IdentityContext
from src.services import reservation
from src.utils.ids import _new_id
from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/servers"
BASE_INT = "/api/server/v1/internal"


# ── Helpers (общие с test_acs_snapshots_batch.py) ────────────────────────────


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


async def _make_managed(make_server, db, *, dept="dep_a"):
    srv = await make_server(department_id=dept)
    srv.is_managed = True
    srv.management_user = "dbos"
    await db.flush()
    return srv


async def _make_os_version(db, *, name: str):
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
    """Перехват `dispatch_task_with_hit` — тот же контракт, что у ACS-dispatch'а."""
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


@pytest.fixture
def failing_dispatch(monkeypatch):
    """`dispatch_task_with_hit` кидает ServiceUnavailableError — worker недоступен."""

    async def fake_dispatch_with_hit(**kwargs):
        raise ServiceUnavailableError(
            error_code="WORKER_UNAVAILABLE", message="worker unreachable",
        )

    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )


class _Recorder(list):
    """list-подкласс, поддерживающий атрибуты (`.stored`)."""


@pytest.fixture
def restore_prepare_capture(monkeypatch):
    """Перехват store_prepare_creds/dispatch_task — авто-prepare после restore."""
    calls: _Recorder = _Recorder()
    stored: dict[str, dict] = {}

    async def fake_store(creds_key, creds):
        stored[creds_key] = creds

    async def fake_delete(creds_key):
        stored.pop(creds_key, None)

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                             created_by, request_id, target_resource_id=None,
                             idempotency_key=None, priority=0):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
        })
        return f"tsk_prepare_{len(calls)}"

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "store_prepare_creds", fake_store)
    monkeypatch.setattr(worker_mod, "delete_prepare_creds", fake_delete)
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    calls.stored = stored  # type: ignore[attr-defined]
    return calls


@pytest.fixture
async def owner_token(make_token, dept_a):
    """Токен фиксированного `usr_prior_owner` — держатель предыдущей брони."""
    return make_token(
        user_id="usr_prior_owner",
        department_id=dept_a,
        service_roles={"server_service": ["operator"]},
    )


# ── busy_note format + capture on single dispatch ────────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestAcsCreateDispatchBusyNoteAndCapture:
    async def test_busy_note_format_and_capture_from_free(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="1711rc42")
        await _enable_acs(db)

        resp = await client.post(
            f"{BASE}/{srv.id}/acs-snapshots",
            headers=_hdr(admin_role_token_a),
            json={"os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.ACS
        assert row.busy_note == "ACS_CREATE_1711rc42"
        assert row.pre_acs_busy_snapshot == {
            "busy_state": "free", "busy_user_id": None,
            "busy_note": None, "busy_since": None,
        }

    async def test_capture_preserves_prior_reservation(
        self, client, admin_role_token_a, make_server, db, captured_dispatch, owner_token,
    ):
        srv = await _make_managed(make_server, db)
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_prior_owner"
        srv.busy_note = "занят под тест X"
        srv.busy_since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await db.flush()
        osv = await _make_os_version(db, name="1711rc43")
        await _enable_acs(db)

        # Админ может стартовать ACS поверх чужой брони (owner брони — не он).
        resp = await client.post(
            f"{BASE}/{srv.id}/acs-snapshots",
            headers=_hdr(admin_role_token_a),
            json={"os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.ACS
        assert row.busy_note == "ACS_CREATE_1711rc43"
        assert row.pre_acs_busy_snapshot == {
            "busy_state": "busy", "busy_user_id": "usr_prior_owner",
            "busy_note": "занят под тест X",
            "busy_since": "2026-01-01T00:00:00+00:00",
        }

    async def test_dispatch_failure_restores_prior_reservation(
        self, client, admin_role_token_a, make_server, db, failing_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_prior_owner"
        srv.busy_note = "занят под тест Y"
        await db.flush()
        osv = await _make_os_version(db, name="1711rc44")
        await _enable_acs(db)

        resp = await client.post(
            f"{BASE}/{srv.id}/acs-snapshots",
            headers=_hdr(admin_role_token_a),
            json={"os_version_id": osv.id},
        )
        assert_error(resp, 503)

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        # Не free — вернулось ровно то, что было до ACS-dispatch'а.
        assert row.busy_state == BusyState.BUSY
        assert row.busy_user_id == "usr_prior_owner"
        assert row.busy_note == "занят под тест Y"
        assert row.pre_acs_busy_snapshot is None


@pytest.mark.usefixtures("soft_dept_mode")
class TestAcsRestoreDispatchBusyNoteFormat:
    async def test_busy_note_format(
        self, client, admin_role_token_a, make_server, db, captured_dispatch,
    ):
        srv = await _make_managed(make_server, db)
        osv = await _make_os_version(db, name="1711rc45")
        await _enable_acs(db)
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/{srv.id}/acs-snapshots/restore",
            headers=_hdr(admin_role_token_a),
            json={"os_version_id": osv.id},
        )
        assert resp.status_code == 202, resp.text

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_note == "ACS_RESTORE_1711rc45"
        assert row.pre_acs_busy_snapshot == {
            "busy_state": "free", "busy_user_id": None,
            "busy_note": None, "busy_since": None,
        }


# ── callback acs-snapshot-created (create) ───────────────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestAcsSnapshotCreatedCallbackRestore:
    async def _seed_in_progress(self, make_server, db, *, dept):
        srv = await _make_managed(make_server, db, dept=dept)
        srv.busy_state = BusyState.ACS
        srv.busy_user_id = "usr_bot"
        srv.busy_note = "ACS_CREATE_1711rc50"
        srv.busy_since = datetime.now(timezone.utc)
        srv.pre_acs_busy_snapshot = {
            "busy_state": "busy", "busy_user_id": "usr_prior_owner",
            "busy_note": "занят под тест Z",
            "busy_since": "2026-01-01T00:00:00+00:00",
        }
        await db.flush()
        return srv

    async def test_success_restores_prior_reservation(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await self._seed_in_progress(make_server, db, dept=dept_a)

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/acs-snapshot-created",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"os_version_id": "osv_x", "succeeded": True, "snapshot_name": "snap1"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "busy"

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.BUSY
        assert row.busy_user_id == "usr_prior_owner"
        assert row.busy_note == "занят под тест Z"
        assert row.pre_acs_busy_snapshot is None

    async def test_failure_also_restores_prior_reservation(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await self._seed_in_progress(make_server, db, dept=dept_a)

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/acs-snapshot-created",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"os_version_id": "osv_x", "succeeded": False, "error": "ACS timeout"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "busy"

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.BUSY
        assert row.busy_user_id == "usr_prior_owner"
        assert row.pre_acs_busy_snapshot is None

    async def test_no_snapshot_falls_back_to_free(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await _make_managed(make_server, db, dept=dept_a)
        srv.busy_state = BusyState.ACS
        srv.busy_note = "ACS_CREATE_1711rc51"
        # pre_acs_busy_snapshot остаётся NULL — данные до миграции / race.
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/acs-snapshot-created",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"os_version_id": "osv_x", "succeeded": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "free"

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.FREE
        assert row.busy_user_id is None
        assert row.busy_note is None


# ── callback acs-snapshot-restore-done (restore) ─────────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestAcsSnapshotRestoreDoneCallback:
    async def _seed_in_progress(self, make_server, db, *, dept, osv_id):
        srv = await _make_managed(make_server, db, dept=dept)
        srv.busy_state = BusyState.ACS
        srv.busy_user_id = "usr_bot"
        srv.busy_note = f"ACS_RESTORE_{osv_id}"
        srv.pre_acs_busy_snapshot = {
            "busy_state": "busy", "busy_user_id": "usr_prior_owner",
            "busy_note": "занят под тест W", "busy_since": None,
        }
        await db.flush()
        return srv

    async def test_failed_restores_prior_reservation(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        osv = await _make_os_version(db, name="1711rc60")
        srv = await self._seed_in_progress(make_server, db, dept=dept_a, osv_id=osv.name)

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/acs-snapshot-restore-done",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"os_version_id": osv.id, "succeeded": False, "error": "restore timed out"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "busy"
        assert resp.json()["prepare_task_id"] is None

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.BUSY
        assert row.busy_user_id == "usr_prior_owner"
        assert row.pre_acs_busy_snapshot is None

    async def test_succeeded_sets_prepare_busy_note_and_dispatches_prepare(
        self, client, worker_bot_token_a, make_server, db, dept_a, restore_prepare_capture,
    ):
        osv = await _make_os_version(db, name="1711rc61")
        await _set_bootstrap_password(db, osv.id)
        srv = await self._seed_in_progress(make_server, db, dept=dept_a, osv_id=osv.name)

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/acs-snapshot-restore-done",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"os_version_id": osv.id, "succeeded": True, "snapshot_name": "snap2"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["busy_state"] == "acs"
        assert body["prepare_task_id"] is not None

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.ACS
        assert row.busy_note == f"ACS_RESTORE_PREPARE_{osv.name}"
        # os_version_id известен точно из payload'а восстановленного снимка —
        # проставляется сразу, не дожидаясь следующего inventory.sync.
        assert row.os_version_id == osv.id
        assert row.os_last_synced_at is not None
        # Снимок прежней брони ЖИВ — снимет только последующий callback `prepared`.
        assert row.pre_acs_busy_snapshot == {
            "busy_state": "busy", "busy_user_id": "usr_prior_owner",
            "busy_note": "занят под тест W", "busy_since": None,
        }
        assert [c["task_kind"] for c in restore_prepare_capture] == ["server.prepare"]

    async def test_succeeded_bootstrap_missing_sets_busy_note(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        osv = await _make_os_version(db, name="1711rc62")
        # bootstrap-пароль НЕ задан — редкий race (стёрли между dispatch и callback).
        srv = await self._seed_in_progress(make_server, db, dept=dept_a, osv_id=osv.name)

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/acs-snapshot-restore-done",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"os_version_id": osv.id, "succeeded": True, "snapshot_name": "snap3"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "acs"

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.ACS
        assert row.busy_note == f"ACS_RESTORE_BOOTSTRAP_MISSING_{osv.name}"
        # Бронь ждёт ручного вмешательства оператора — снимок НЕ снят.
        assert row.pre_acs_busy_snapshot is not None


# ── callback prepared: конец цепочки restore→prepare ─────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestRecordServerPreparedRestoresAcsState:
    async def test_prepared_after_restore_restores_prior_reservation(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await _make_managed(make_server, db, dept=dept_a)
        srv.busy_state = BusyState.ACS
        srv.busy_note = "ACS_RESTORE_PREPARE_1711rc70"
        srv.pre_acs_busy_snapshot = {
            "busy_state": "testing", "busy_user_id": "usr_prior_owner",
            "busy_note": "прогон теста T", "busy_since": None,
        }
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"management_user": "dbos"},
        )
        assert resp.status_code == 200, resp.text

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.is_managed is True
        assert row.busy_state == BusyState.TESTING
        assert row.busy_user_id == "usr_prior_owner"
        assert row.busy_note == "прогон теста T"
        assert row.pre_acs_busy_snapshot is None

    async def test_regular_prepare_not_touched_by_acs_restore(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        # Обычный (не после ACS) prepare — busy_state свободен, ветка не заходит.
        srv = await make_server(department_id=dept_a)
        await db.flush()

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(worker_bot_token_a, dept=dept_a),
            json={"management_user": "dbos"},
        )
        assert resp.status_code == 200, resp.text

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.busy_state == BusyState.FREE


# ── unit-тесты чистых функций reservation.py ─────────────────────────────────


def _local_server(**overrides) -> Server:
    defaults: dict = dict(
        id="srv_unit_test",
        hostname="unit-test-host",
        ip_address=IPv4Address("10.0.0.1"),
        department_id="dep_a",
        busy_state=BusyState.FREE,
        busy_user_id=None,
        busy_note=None,
        busy_since=None,
        pre_acs_busy_snapshot=None,
    )
    defaults.update(overrides)
    return Server(**defaults)


def _identity(**overrides) -> IdentityContext:
    defaults: dict = dict(
        user_id="usr_caller", username="caller", department_id="dep_a",
        service_roles={}, platform_role=None,
    )
    defaults.update(overrides)
    return IdentityContext(**defaults)


class TestCapturePreAcsStateUnit:
    def test_captures_current_fields(self):
        srv = _local_server(
            busy_state=BusyState.BUSY, busy_user_id="usr_x", busy_note="note",
            busy_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        snap = reservation.capture_pre_acs_state(srv)
        assert snap == {
            "busy_state": "busy", "busy_user_id": "usr_x",
            "busy_note": "note", "busy_since": "2026-01-01T00:00:00+00:00",
        }

    def test_free_state_has_null_since(self):
        snap = reservation.capture_pre_acs_state(_local_server())
        assert snap == {
            "busy_state": "free", "busy_user_id": None,
            "busy_note": None, "busy_since": None,
        }


class TestRestorePreAcsStateUnit:
    def test_restores_from_snapshot_and_clears_it(self):
        srv = _local_server(
            busy_state=BusyState.ACS, busy_note="ACS_CREATE_x",
            pre_acs_busy_snapshot={
                "busy_state": "busy", "busy_user_id": "usr_owner",
                "busy_note": "prior note", "busy_since": "2026-01-01T00:00:00+00:00",
            },
        )
        reservation.restore_pre_acs_state(srv)
        assert srv.busy_state == "busy"
        assert srv.busy_user_id == "usr_owner"
        assert srv.busy_note == "prior note"
        assert srv.busy_since == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert srv.pre_acs_busy_snapshot is None

    def test_falls_back_to_free_when_snapshot_missing(self):
        srv = _local_server(busy_state=BusyState.ACS, busy_user_id="usr_x", busy_note="ACS_CREATE_x")
        reservation.restore_pre_acs_state(srv)
        assert srv.busy_state == BusyState.FREE
        assert srv.busy_user_id is None
        assert srv.busy_note is None
        assert srv.busy_since is None
        assert srv.pre_acs_busy_snapshot is None

    def test_falls_back_to_free_when_snapshot_malformed(self):
        srv = _local_server(
            busy_state=BusyState.ACS, busy_note="ACS_CREATE_x",
            pre_acs_busy_snapshot={"busy_since": "not-a-date"},
        )
        reservation.restore_pre_acs_state(srv)
        assert srv.busy_state == BusyState.FREE
        assert srv.busy_user_id is None
        assert srv.pre_acs_busy_snapshot is None


class TestEnsureNotAcsLockedUnit:
    def test_noop_when_not_acs(self):
        srv = _local_server(busy_state=BusyState.BUSY)
        reservation.ensure_not_acs_locked(_identity(), srv)  # no raise

    def test_department_admin_of_same_dept_passes(self):
        srv = _local_server(busy_state=BusyState.ACS, department_id="dep_a")
        identity = _identity(
            platform_role=PlatformRole.DEPARTMENT_ADMIN, department_id="dep_a",
        )
        reservation.ensure_not_acs_locked(identity, srv)  # no raise

    def test_service_admin_role_passes(self):
        srv = _local_server(busy_state=BusyState.ACS, department_id="dep_a")
        identity = _identity(service_roles={"server_service": ["admin"]})
        reservation.ensure_not_acs_locked(identity, srv)  # no raise

    def test_non_admin_blocked(self):
        srv = _local_server(busy_state=BusyState.ACS, department_id="dep_a")
        with pytest.raises(ConflictError) as exc_info:
            reservation.ensure_not_acs_locked(_identity(), srv)
        assert exc_info.value.error_code == "SERVER_ACS_BUSY"

    def test_department_admin_of_other_dept_blocked(self):
        srv = _local_server(busy_state=BusyState.ACS, department_id="dep_a")
        identity = _identity(
            platform_role=PlatformRole.DEPARTMENT_ADMIN, department_id="dep_b",
        )
        with pytest.raises(ConflictError):
            reservation.ensure_not_acs_locked(identity, srv)
