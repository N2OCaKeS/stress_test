"""Интеграционные тесты per-server управляющих кред (#3).

Покрывает контракт волны A:
* prepare кладёт `mgmt_install` в bootstrap-stash и сохраняет шифрованные
  креды в БД (`mgmt_*`, pending_apply=True);
* internal fetch `GET /internal/servers/{id}/management/credentials` — RBAC
  (worker_bot 200, reader 403), reveal-audit WARNING, current vs previous;
* rotate `POST /servers/{id}/management-credentials/rotate` — pending + previous
  + stash с новым материалом;
* applied-callback `POST /internal/.../management-credentials/applied` — снимает
  pending, зануляет previous, ставит rotated_at.
"""
from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from src.models import Server
from src.services import management_creds as mgmt_svc
from tests._helpers import assert_error, auth_hdr as _hdr, make_dispatch_capture, make_emit_capture

BASE = "/api/server/v1/servers"
BASE_INT = "/api/server/v1/internal"


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


class _Recorder(list):
    """list, поддерживающий атрибуты (`.stored`) — list напрямую не даёт."""


@pytest.fixture
def prepare_capture(monkeypatch):
    """Dispatch-capture + перехват store_prepare_creds (plaintext-stash)."""
    calls = _Recorder()
    stored: dict[str, dict] = {}
    calls.stored = stored  # type: ignore[attr-defined]

    async def fake_store(creds_key, creds):
        stored[creds_key] = creds

    async def fake_delete(creds_key):
        stored.pop(creds_key, None)

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0,
                            return_hit=False):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "store_prepare_creds", fake_store)
    monkeypatch.setattr(worker_mod, "delete_prepare_creds", fake_delete)
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    return calls


# ── prepare: mgmt_install в stash + шифрованные креды в БД ────────────────────


class TestPrepareGeneratesManagementCreds:
    async def test_prepare_stashes_mgmt_install_and_saves_db(
        self, client, operator_token_a, make_server, prepare_capture, db,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp.status_code == 202, resp.text
        creds_key = prepare_capture[0]["payload"]["bootstrap_creds_key"]
        mgmt_install = prepare_capture.stored[creds_key]["mgmt_install"]
        assert mgmt_install["management_user"] == "dbos"
        assert mgmt_install["public_key"].startswith("ssh-ed25519 ")
        assert "PRIVATE KEY" in mgmt_install["private_key"]
        assert mgmt_install["password"]
        # В payload plaintext не уходит — только ссылка на stash.
        assert "mgmt_install" not in prepare_capture[0]["payload"]

        # В БД сохранён шифрованный материал, pending_apply=True.
        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.mgmt_ssh_public_key == mgmt_install["public_key"]
        assert row.mgmt_ssh_private_key_encrypted is not None
        assert row.mgmt_password_encrypted is not None
        assert row.mgmt_creds_pending_apply is True
        # ciphertext, не plaintext.
        assert mgmt_install["private_key"] not in row.mgmt_ssh_private_key_encrypted

    async def test_generated_audit_emitted(
        self, client, operator_token_a, make_server, prepare_capture, monkeypatch,
    ):
        emits = make_emit_capture(
            monkeypatch,
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
        )
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp.status_code == 202, resp.text
        gen = [e for e in emits if e["action"] == "server.management_creds_generated"]
        assert len(gen) == 1
        assert gen[0]["details"]["management_user"] == "dbos"


# ── internal fetch: RBAC + reveal-audit + current/previous ───────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestFetchManagementCredentials:
    async def _seed(self, db, make_server):
        srv = await make_server(department_id="dep_a")
        _, creds, _ = await mgmt_svc.ensure_management_credentials(db, srv)
        srv.is_managed = True
        srv.management_user = "dbos"
        srv.mgmt_creds_pending_apply = False
        await db.flush()
        return srv, creds

    async def test_worker_bot_fetches_current(
        self, client, worker_bot_token_a, make_server, db,
    ):
        srv, creds = await self._seed(db, make_server)
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/management/credentials",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["management_user"] == "dbos"
        assert body["ssh_private_key"] == creds["private_key"]
        assert body["password"] == creds["password"]

    async def test_reader_forbidden(
        self, client, reader_token_a, make_server, db,
    ):
        srv, _ = await self._seed(db, make_server)
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/management/credentials",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_creds_stored_404(
        self, client, worker_bot_token_a, make_server, db,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/management/credentials",
            headers=_hdr(worker_bot_token_a),
        )
        assert_error(resp, 404, "MANAGEMENT_CREDS_NOT_FOUND")

    async def test_pending_returns_previous(
        self, client, worker_bot_token_a, make_server, db,
    ):
        srv, creds_old = await self._seed(db, make_server)
        # Ротация: новый материал в current, старый — в previous, pending=True.
        new_creds = await mgmt_svc.rotate_management_credentials(db, srv)
        await db.flush()
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/management/credentials",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Пока pending — отдаётся previous (рабочий на боксе), не новый.
        assert body["ssh_private_key"] == creds_old["private_key"]
        assert body["ssh_private_key"] != new_creds["private_key"]

    async def test_reveal_audit_warning_emitted(
        self, client, worker_bot_token_a, make_server, db, monkeypatch,
    ):
        srv, _ = await self._seed(db, make_server)
        emits = make_emit_capture(monkeypatch)
        resp = await client.get(
            f"{BASE_INT}/servers/{srv.id}/management/credentials",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        reveal = [e for e in emits if e["action"] == "server.management_credentials_revealed"]
        assert len(reveal) == 1
        assert reveal[0]["status"] == "success"
        assert reveal[0]["details"]["source"] == "current"


# ── rotate dispatch + applied callback ───────────────────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestRotateManagementCredentials:
    async def _seed_prepared(self, db, make_server):
        srv = await make_server(department_id="dep_a")
        _, creds, _ = await mgmt_svc.ensure_management_credentials(db, srv)
        srv.is_managed = True
        srv.management_user = "dbos"
        srv.mgmt_creds_pending_apply = False
        await db.flush()
        return srv, creds

    async def test_rotate_dispatch_sets_pending_and_previous(
        self, client, operator_token_a, make_server, db, monkeypatch,
    ):
        srv, creds_old = await self._seed_prepared(db, make_server)
        calls = make_dispatch_capture(monkeypatch)
        resp = await client.post(
            f"{BASE}/{srv.id}/management-credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["task_id"].startswith("tsk_")
        assert calls[0]["task_kind"] == "server.rotate_management_creds"
        assert calls[0]["payload"]["creds_stash_key"].startswith("dbos:dispatch_creds:")

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.mgmt_creds_pending_apply is True
        # previous держит старый ciphertext.
        assert row.previous_mgmt_ssh_private_key_encrypted is not None
        from src.services import secrets_service
        assert secrets_service.decrypt(
            row.previous_mgmt_ssh_private_key_encrypted,
            aad=secrets_service.aad_for_server_mgmt_ssh_key(row.id),
        ) == creds_old["private_key"]

    async def test_rotate_rejected_when_pending(
        self, client, operator_token_a, make_server, db, monkeypatch,
    ):
        # Первая ротация повесила pending. Повторный dispatch до applied должен
        # отбиться 409, не тронув previous (реально стоящий на боксе ключ).
        srv, creds_old = await self._seed_prepared(db, make_server)
        make_dispatch_capture(monkeypatch)
        first = await client.post(
            f"{BASE}/{srv.id}/management-credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert first.status_code == 202, first.text
        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.mgmt_creds_pending_apply is True
        previous_before = row.previous_mgmt_ssh_private_key_encrypted
        current_before = row.mgmt_ssh_private_key_encrypted

        second = await client.post(
            f"{BASE}/{srv.id}/management-credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert_error(second, 409, "MGMT_ROTATION_PENDING")

        row2 = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row2.previous_mgmt_ssh_private_key_encrypted == previous_before
        assert row2.mgmt_ssh_private_key_encrypted == current_before

    async def test_rotate_race_second_blocked_by_row_lock(
        self, client, operator_token_a, make_server, db, monkeypatch,
    ):
        # Гонка: параллельная ротация закоммитила pending_apply=True между нашим
        # visibility-чтением и проверкой флага. Без row-lock stale-read пропустил
        # бы вторую ротацию → перенос current→previous затёр бы реально стоящий
        # на боксе ключ. get_for_update перечитывает свежее → 409, previous цел.
        srv, creds_old = await self._seed_prepared(db, make_server)
        make_dispatch_capture(monkeypatch)

        from sqlalchemy import update as sa_update

        from src.services import server as server_svc

        row_before = (
            await db.execute(select(Server).where(Server.id == srv.id))
        ).scalar_one()
        prev_before = row_before.previous_mgmt_ssh_private_key_encrypted
        cur_before = row_before.mgmt_ssh_private_key_encrypted

        original_get = server_svc.get_server

        async def racy_get(db_, identity, sid):
            obj = await original_get(db_, identity, sid)
            await db_.execute(
                sa_update(Server).where(Server.id == sid).values(
                    mgmt_creds_pending_apply=True,
                )
            )
            await db_.commit()
            return obj

        monkeypatch.setattr(server_svc, "get_server", racy_get)

        resp = await client.post(
            f"{BASE}/{srv.id}/management-credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "MGMT_ROTATION_PENDING")

        row_after = (
            await db.execute(select(Server).where(Server.id == srv.id))
        ).scalar_one()
        # previous не затёрт, current не тронут — реально стоящий ключ сохранён.
        assert row_after.previous_mgmt_ssh_private_key_encrypted == prev_before
        assert row_after.mgmt_ssh_private_key_encrypted == cur_before

    async def test_rotate_blocked_while_updating(
        self, client, operator_token_a, make_server, db, monkeypatch,
    ):
        # Ротация ключа посреди astra-update деструктивна для активной SSH-сессии
        # обновления — гейт updating обязан её отбить.
        from src.core.constants import BusyState

        srv, _ = await self._seed_prepared(db, make_server)
        srv.busy_state = BusyState.UPDATING
        await db.flush()
        make_dispatch_capture(monkeypatch)

        resp = await client.post(
            f"{BASE}/{srv.id}/management-credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "SERVER_UPDATING")

    async def test_rotate_requires_prepared(
        self, client, operator_token_a, make_server, db, monkeypatch,
    ):
        srv = await make_server(department_id="dep_a")  # is_managed=False
        make_dispatch_capture(monkeypatch)
        resp = await client.post(
            f"{BASE}/{srv.id}/management-credentials/rotate",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 409, "PREPARE_REQUIRED")

    async def test_reader_cannot_rotate(
        self, client, reader_token_a, make_server, db, monkeypatch,
    ):
        srv, _ = await self._seed_prepared(db, make_server)
        make_dispatch_capture(monkeypatch)
        resp = await client.post(
            f"{BASE}/{srv.id}/management-credentials/rotate",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_applied_callback_clears_pending(
        self, client, worker_bot_token_a, make_server, db,
    ):
        srv, _ = await self._seed_prepared(db, make_server)
        await mgmt_svc.rotate_management_credentials(db, srv)
        await db.flush()
        assert srv.mgmt_creds_pending_apply is True

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/management-credentials/applied",
            headers=_hdr(worker_bot_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

        row = (await db.execute(select(Server).where(Server.id == srv.id))).scalar_one()
        assert row.mgmt_creds_pending_apply is False
        assert row.previous_mgmt_ssh_private_key_encrypted is None
        assert row.previous_mgmt_password_encrypted is None
        assert row.mgmt_creds_rotated_at is not None
