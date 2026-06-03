"""Coverage gaps — server_service w17.

Areas:
* view_credentials GET /ipmi/credentials — audit branches at unit level:
  - require_action AuthorizationError → denied, reason=permission_denied
  - load_visible_server NotFoundError → failure, reason=not_found_or_cross_dept
  - ipmi_repo returns None → failure, reason=not_registered
  - success → audit action=ipmi_controller.view_credentials_meta
* power_status GET /ipmi/power — audit branches at unit level:
  - require_action AuthorizationError → denied, reason=permission_denied
  - load_visible_server NotFoundError → failure, reason=not_found_or_cross_dept
  - success → audit action=server.power_status_cached with power_state
* server_prepare_dispatch:
  - AuthorizationError from get_server (no VIEW) → denied audit, no store_prepare_creds
  - dispatch_task returns idempotent_hit=True (race path) → delete_prepare_creds cleanup
"""

from __future__ import annotations

import base64 as _b64_mod

import pytest

from src.core.exceptions import (
    AuthorizationError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.schemas.identity import IdentityContext


BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_identity(
    *,
    user_id: str = "usr_test",
    department_id: str | None = "dep_a",
    service_roles: dict | None = None,
    subject_type: str = "user",
) -> IdentityContext:
    return IdentityContext(
        user_id=user_id,
        username="tester",
        department_id=department_id,
        department_name=None,
        allowed_services=["server_service"],
        service_roles=service_roles or {"server_service": ["admin"]},
        is_banned=False,
        platform_role=None,
        subject_type=subject_type,
    )


def _capture_emits(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr("src.api.v1.endpoints.ipmi.audit_service.emit", fake_emit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit", fake_emit,
    )
    monkeypatch.setattr("src.services.server.audit_service.emit", fake_emit)
    return captured


# ─────────────────────────────────────────────────────────────────────────────
# 1. view_credentials GET — audit branches
# ─────────────────────────────────────────────────────────────────────────────


class TestViewCredentialsMetaAudit:
    """GET /servers/{id}/ipmi/credentials: audit emit на всех ветках."""

    @pytest.fixture
    def captured(self, monkeypatch) -> list[dict]:
        return _capture_emits(monkeypatch)

    async def test_no_permission_emits_denied_audit(
        self, client, make_server, operator_token_a, captured,
    ):
        """operator без view_credentials → require_action кидает AuthorizationError
        → audit denied, reason=permission_denied."""
        srv = await make_server(department_id="dep_a")

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 403

        denied = [
            e for e in captured
            if e["action"] == "ipmi_controller.view_credentials_meta"
            and e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured
        ev = denied[0]
        assert ev["allowed"] is False
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "ipmi_controller"

    async def test_server_not_found_emits_failure_audit(
        self, client, make_server, make_token, dept_a, monkeypatch, captured,
    ):
        """admin_with_view_credentials: load_visible_server → NotFoundError
        → audit failure, reason=not_found_or_cross_dept."""
        srv = await make_server(department_id=dept_a)
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )

        async def _raise_not_found(*args, **kwargs):
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND",
                message="not visible",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.ipmi.server_svc.load_visible_server",
            _raise_not_found,
        )

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(tok),
        )
        assert resp.status_code == 404

        failures = [
            e for e in captured
            if e["action"] == "ipmi_controller.view_credentials_meta"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured
        ev = failures[0]
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"
        assert ev["target_id"] == srv.id

    async def test_no_ipmi_row_emits_failure_audit(
        self, client, make_server, make_token, dept_a, captured,
    ):
        """admin: сервер виден, но ipmi_controllers row отсутствует
        → audit failure, reason=not_registered."""
        srv = await make_server(department_id=dept_a)
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(tok),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "NO_IPMI_CONTROLLER"

        failures = [
            e for e in captured
            if e["action"] == "ipmi_controller.view_credentials_meta"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured
        ev = failures[0]
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_registered"
        assert "department_id" in ev["details"]

    async def test_success_emits_view_credentials_meta_audit(
        self, client, make_server, make_ipmi, make_token, dept_a, captured,
    ):
        """admin + IPMI row → 200 + audit success для view_credentials_meta."""
        srv = await make_server(department_id=dept_a)
        await make_ipmi(server_id=srv.id, username="bmc_user")
        tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(tok),
        )
        assert resp.status_code == 200

        successes = [
            e for e in captured
            if e["action"] == "ipmi_controller.view_credentials_meta"
            and e.get("status") == "success"
        ]
        assert len(successes) == 1, captured
        ev = successes[0]
        assert ev["allowed"] is True
        assert ev["details"]["server_id"] == srv.id
        assert "department_id" in ev["details"]

    async def test_no_denied_on_permission_error(
        self, client, make_server, reader_token_a, captured,
    ):
        """reader без view_credentials → denied присутствует, success — нет."""
        srv = await make_server(department_id="dep_a")
        await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/credentials",
            headers=_hdr(reader_token_a),
        )
        successes = [
            e for e in captured
            if e["action"] == "ipmi_controller.view_credentials_meta"
            and e.get("status") == "success"
        ]
        assert successes == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. power_status GET — audit branches
# ─────────────────────────────────────────────────────────────────────────────


class TestPowerStatusCachedAudit:
    """GET /servers/{id}/ipmi/power: audit emit на всех ветках."""

    @pytest.fixture
    def captured(self, monkeypatch) -> list[dict]:
        return _capture_emits(monkeypatch)

    async def test_no_view_emits_denied_audit(
        self, client, make_server, guest_token_a, captured,
    ):
        """guest без view → require_action кидает AuthorizationError
        → audit denied, reason=permission_denied."""
        srv = await make_server(department_id="dep_a")

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/power",
            headers=_hdr(guest_token_a),
        )
        assert resp.status_code == 403

        denied = [
            e for e in captured
            if e["action"] == "server.power_status_cached"
            and e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured
        ev = denied[0]
        assert ev["allowed"] is False
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"

    async def test_cross_dept_emits_failure_audit(
        self, client, make_server, make_token, dept_a, dept_b, monkeypatch, captured,
    ):
        """reader с VIEW, но load_visible_server → NotFoundError
        → audit failure, reason=not_found_or_cross_dept."""
        srv = await make_server(department_id=dept_a)
        tok = make_token(
            department_id=dept_b,
            service_roles={"server_service": ["reader"]},
        )

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/power",
            headers=_hdr(tok),
        )
        assert resp.status_code == 404

        failures = [
            e for e in captured
            if e["action"] == "server.power_status_cached"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured
        ev = failures[0]
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"

    async def test_success_emits_power_status_cached_audit(
        self, client, make_server, reader_token_a, db, captured,
    ):
        """reader + сервер виден → 200 + audit success power_status_cached."""
        srv = await make_server(department_id="dep_a")
        srv.power_state = "on"
        await db.flush()

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/power",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 200

        successes = [
            e for e in captured
            if e["action"] == "server.power_status_cached"
            and e.get("status") == "success"
        ]
        assert len(successes) == 1, captured
        ev = successes[0]
        assert ev["allowed"] is True
        assert ev["details"]["power_state"] == "on"
        assert "department_id" in ev["details"]

    async def test_success_audit_contains_power_state_unknown(
        self, client, make_server, reader_token_a, captured,
    ):
        """Дефолтный power_state=unknown тоже попадает в success-аудит."""
        srv = await make_server(department_id="dep_a")

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/power",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 200

        successes = [
            e for e in captured
            if e["action"] == "server.power_status_cached"
            and e.get("status") == "success"
        ]
        assert len(successes) == 1
        assert successes[0]["details"]["power_state"] == "unknown"

    async def test_denied_emits_no_success(
        self, client, make_server, guest_token_a, captured,
    ):
        """guest denied → нет success-аудита."""
        srv = await make_server(department_id="dep_a")
        await client.get(
            f"{BASE}/servers/{srv.id}/ipmi/power",
            headers=_hdr(guest_token_a),
        )
        successes = [
            e for e in captured
            if e["action"] == "server.power_status_cached"
            and e.get("status") == "success"
        ]
        assert successes == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. server_prepare_dispatch — AuthorizationError (no VIEW) branch
# ─────────────────────────────────────────────────────────────────────────────


def _b64(s: str) -> str:
    return _b64_mod.b64encode(s.encode()).decode()


class TestPrepareDispatchNoViewPermission:
    """server_prepare_dispatch: get_server → AuthorizationError → denied audit.

    Caller прошёл require_action(UPDATE), но get_server кидает AuthorizationError
    из-за отсутствия VIEW. Итог: denied+allowed=False, reason=no_view_permission.
    store_prepare_creds не должен вызываться до проверки видимости.
    """

    @pytest.fixture
    def captured_prepare(self, monkeypatch) -> list[dict]:
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, "actor_id": actor_id, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit", fake_emit,
        )
        return captured

    @pytest.fixture
    def stored_keys(self, monkeypatch) -> list[str]:
        keys: list[str] = []

        async def fake_store(creds_key, creds):
            keys.append(creds_key)

        import src.services.worker_client as wm
        monkeypatch.setattr(wm, "store_prepare_creds", fake_store)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.store_prepare_creds",
            fake_store,
        )
        return keys

    async def test_no_view_returns_403(
        self, client, make_server, operator_token_a, monkeypatch,
        captured_prepare, stored_keys,
    ):
        """get_server → AuthorizationError → 403."""
        srv = await make_server(department_id="dep_a")

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            _raise_authz,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )
        assert resp.status_code == 403

    async def test_no_view_emits_denied_audit(
        self, client, make_server, operator_token_a, monkeypatch,
        captured_prepare, stored_keys,
    ):
        """AuthorizationError → audit denied+allowed=False, reason=no_view_permission."""
        srv = await make_server(department_id="dep_a")

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            _raise_authz,
        )

        await client.post(
            f"{BASE}/servers/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )

        denied = [
            e for e in captured_prepare
            if e["action"] == "server.prepare" and e.get("status") == "denied"
        ]
        assert len(denied) == 1, captured_prepare
        ev = denied[0]
        assert ev["allowed"] is False
        assert ev["details"]["reason"] == "no_view_permission"
        assert ev["target_id"] == srv.id

    async def test_no_view_does_not_store_creds(
        self, client, make_server, operator_token_a, monkeypatch,
        captured_prepare, stored_keys,
    ):
        """AuthorizationError → store_prepare_creds НЕ вызывается.

        Важная инварианта безопасности: plaintext в Redis не попадает до
        прохождения всех permission/visibility проверок.
        """
        srv = await make_server(department_id="dep_a")

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            _raise_authz,
        )

        await client.post(
            f"{BASE}/servers/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )

        assert stored_keys == [], "store_prepare_creds не должен вызываться до visibility-check"

    async def test_no_view_emits_no_success(
        self, client, make_server, operator_token_a, monkeypatch,
        captured_prepare, stored_keys,
    ):
        """AuthorizationError → нет success-аудита."""
        srv = await make_server(department_id="dep_a")

        async def _raise_authz(*args, **kwargs):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED",
                message="no VIEW",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.get_server",
            _raise_authz,
        )

        await client.post(
            f"{BASE}/servers/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )

        successes = [
            e for e in captured_prepare
            if e["action"] == "server.prepare" and e.get("status") == "success"
        ]
        assert successes == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. server_prepare_dispatch — race idempotent_hit=True → delete_prepare_creds
# ─────────────────────────────────────────────────────────────────────────────


class TestPrepareDispatchIdempotentHitRaceCleanup:
    """dispatch_task возвращает idempotent_hit=True (race path).

    Сценарий: pre-check (_get_task_by_idempotency_key) вернул None,
    store_prepare_creds выполнился, но dispatch_task обнаружил гонку
    с конкурентом — вернул (existing_task_id, True). Наш только что
    положенный stash теперь orphan-кред. Endpoint обязан вызвать
    delete_prepare_creds для cleanup.
    """

    @pytest.fixture
    def prepare_spy(self, monkeypatch):
        stored: dict[str, dict] = {}
        stored_keys: list[str] = []
        deleted_keys: list[str] = []

        async def fake_store(creds_key, creds):
            stored[creds_key] = creds
            stored_keys.append(creds_key)

        async def fake_delete(creds_key):
            deleted_keys.append(creds_key)

        race_task_id = "tsk_race_winner_existing"

        async def fake_dispatch(
            *, db=None, task_kind, target_server_id, payload,
            created_by, request_id,
            target_resource_id=None, idempotency_key=None,
            return_hit=False,
        ):
            if return_hit:
                return race_task_id, True
            return f"tsk_{task_kind.replace('.', '_')}_fake"

        async def fake_lookup(key):
            return None

        async def fake_ensure_matches(**kwargs):
            pass

        import src.services.worker_client as wm
        monkeypatch.setattr(wm, "store_prepare_creds", fake_store)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.store_prepare_creds",
            fake_store,
        )
        monkeypatch.setattr(wm, "delete_prepare_creds", fake_delete)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.delete_prepare_creds",
            fake_delete,
        )
        async def fake_dispatch_with_hit(**kwargs):
            kwargs["return_hit"] = True
            return await fake_dispatch(**kwargs)

        monkeypatch.setattr(wm, "dispatch_task", fake_dispatch)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            fake_dispatch,
        )
        monkeypatch.setattr(wm, "dispatch_task_with_hit", fake_dispatch_with_hit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            fake_dispatch_with_hit,
        )
        monkeypatch.setattr(wm, "_get_task_by_idempotency_key", fake_lookup)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client._get_task_by_idempotency_key",
            fake_lookup,
        )

        class Spy:
            stored_creds = stored_keys
            deleted = deleted_keys
            task_id = race_task_id

        return Spy()

    async def test_race_idempotent_hit_returns_existing_task_id(
        self, client, make_server, operator_token_a, prepare_spy,
    ):
        """dispatch_task → idempotent_hit=True → endpoint возвращает существующий task_id."""
        srv = await make_server(department_id="dep_a")

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/prepare",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "ik-race-test"},
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["task_id"] == prepare_spy.task_id

    async def test_race_idempotent_hit_deletes_orphan_creds(
        self, client, make_server, operator_token_a, prepare_spy,
    ):
        """Race-idempotent_hit=True → store_prepare_creds был вызван, и тот же ключ
        должен попасть в delete_prepare_creds для очистки orphan-stash."""
        srv = await make_server(department_id="dep_a")

        await client.post(
            f"{BASE}/servers/{srv.id}/prepare",
            headers={**_hdr(operator_token_a), "Idempotency-Key": "ik-race-cleanup"},
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )

        assert len(prepare_spy.stored_creds) == 1, "store должен быть вызван ровно раз"
        stored_key = prepare_spy.stored_creds[0]
        assert stored_key in prepare_spy.deleted, (
            f"ключ {stored_key!r} должен быть удалён после race-hit, "
            f"deleted={prepare_spy.deleted}"
        )

    async def test_race_idempotent_hit_without_idempotency_key(
        self, client, make_server, operator_token_a, prepare_spy,
    ):
        """Без Idempotency-Key в header: pre-check пропускается,
        dispatch_task всё равно может вернуть idempotent_hit=True.
        Endpoint должен отдать 202 с task_id от победителя гонки."""
        srv = await make_server(department_id="dep_a")

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["task_id"] == prepare_spy.task_id
        assert prepare_spy.stored_creds[0] in prepare_spy.deleted
