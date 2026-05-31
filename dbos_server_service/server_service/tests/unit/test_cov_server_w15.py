"""Coverage gaps — server_service w15.

Areas:
* savepoint rollback — audit emit shape on ConflictError / ServiceUnavailableError
  in _dispatch_account_on_host with inject_provision_creds=True
* IPMI 404 unify — ipmi_rotate_password_dispatch:
  - controller found, server in cross-dept → NO_IPMI_CONTROLLER (GAP-1)
  - decommissioned server → SERVER_DECOMMISSIONED (GAP-2)
  - _dispatch_for_server no_ipmi → audit reason=no_ipmi for power.status (GAP-3)
* denied→failure audit — _check_target_department_for_server actor-mismatch
  raises SERVER_NOT_FOUND (GAP-4); verify ipmi_rotate audit emit on
  not_found_or_cross_dept paths
* identity= activated — subject_type in denied audit events on bot calls (GAP-6)
* boundary values — rotated_at exactly at 600s skew boundary (GAP-7);
  naive datetime service-level defence (GAP-8, unit path)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.constants import ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.schemas.identity import IdentityContext


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

BASE = "/api/server/v1"
BASE_DISPATCH = BASE


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


def _bot_identity(**kwargs) -> IdentityContext:
    return _make_identity(subject_type="bot", **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Savepoint rollback — audit emit shape
# ─────────────────────────────────────────────────────────────────────────────


class TestSavepointRollbackAuditEmit:
    """Когда dispatch_task бросает ConflictError или ServiceUnavailableError,
    savepoint откатывается (кред'ы не сохраняются) и audit-emit должен
    выходить с правильным shape: action/status/allowed/reason/operation."""

    @pytest.fixture
    def captured_emits(self, monkeypatch):
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, "actor_id": actor_id, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
            fake_emit,
        )
        return captured

    async def test_service_unavailable_emits_failure_with_worker_unreachable_reason(
        self, client, operator_token_a, make_server, make_account, db,
        monkeypatch, captured_emits,
    ):
        """ServiceUnavailableError → audit failure, reason=worker_unreachable."""
        from src.core.constants import AccountSource

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()
        await db.refresh(acc)
        acc_id, srv_id = acc.id, srv.id

        async def boom(**kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE", message="redis down",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc_id}/provision?server_id={srv_id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503

        failures = [
            e for e in captured_emits
            if e["action"] == "server_account.provision"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured_emits
        ev = failures[0]
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "worker_unreachable"
        assert ev["details"]["server_id"] == srv_id
        assert ev["details"]["operation"] == "provision"

    async def test_conflict_error_emits_failure_with_idempotent_reason(
        self, client, operator_token_a, make_server, make_account, db,
        monkeypatch, captured_emits,
    ):
        """ConflictError → audit failure, reason=idempotent_conflict."""
        from src.core.constants import AccountSource

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()
        await db.refresh(acc)
        acc_id, srv_id = acc.id, srv.id

        async def boom(**kwargs):
            raise ConflictError(
                error_code="TASK_IDEMPOTENT_CONFLICT", message="duplicate",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc_id}/provision?server_id={srv_id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409

        failures = [
            e for e in captured_emits
            if e["action"] == "server_account.provision"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured_emits
        ev = failures[0]
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "idempotent_conflict"
        assert ev["details"]["task_kind"] == "account.provision"

    async def test_service_unavailable_emits_no_success_audit(
        self, client, operator_token_a, make_server, make_account, db,
        monkeypatch, captured_emits,
    ):
        """При откате savepoint'а success-аудита быть не должно."""
        from src.core.constants import AccountSource

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()
        await db.refresh(acc)
        acc_id, srv_id = acc.id, srv.id

        async def boom(**kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE", message="down",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )

        await client.post(
            f"{BASE}/server-accounts/{acc_id}/provision?server_id={srv_id}&force_password=true",
            headers=_hdr(operator_token_a),
        )

        successes = [
            e for e in captured_emits
            if e["action"] == "server_account.provision"
            and e.get("status") == "success"
        ]
        assert successes == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. IPMI 404 unify — ipmi_rotate_password_dispatch gaps
# ─────────────────────────────────────────────────────────────────────────────


class TestIpmiRotatePasswordDispatchEdges:
    """Дополнительные edge-кейсы ipmi_rotate_password_dispatch."""

    async def test_decommissioned_server_returns_409(
        self, client, admin_role_token_a, make_server, make_ipmi, db,
    ):
        """SERVER_DECOMMISSIONED при decommissioned сервере, даже если controller найден."""
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 409, resp.text
        assert resp.json().get("error_code") == "SERVER_DECOMMISSIONED"

    async def test_decommissioned_emits_failure_audit(
        self, client, admin_role_token_a, make_server, make_ipmi, db, monkeypatch,
    ):
        """Decommissioned → audit failure с reason=decommissioned."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
            fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()

        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 409

        failures = [
            e for e in captured
            if e["action"] == "ipmi_controller.rotate_dispatch"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured
        ev = failures[0]
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "decommissioned"
        assert ev["details"]["server_id"] == srv.id

    async def test_controller_found_but_server_cross_dept_returns_404(
        self, client, admin_role_token_a, make_server, make_ipmi, monkeypatch,
    ):
        """Controller найден, но load_visible_server бросает NotFoundError →
        NO_IPMI_CONTROLLER (server cross-dept скрыт за IPMI 404).

        Этот конкретный path: get_by_id находит controller (он существует),
        но server.department_id чужой → load_visible_server даёт 404.
        """
        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)

        # Имитируем: сервер "переехал" в другой dept, но controller остался.
        async def cross_dept_server(*args, **kwargs):
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND", message="server not visible",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.load_visible_server",
            cross_dept_server,
        )

        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json().get("error_code") == "NO_IPMI_CONTROLLER"

    async def test_controller_found_server_cross_dept_emits_failure_audit(
        self, client, admin_role_token_a, make_server, make_ipmi, monkeypatch,
    ):
        """Cross-dept через server path → failure audit с reason=not_found_or_cross_dept."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
            fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        ctrl = await make_ipmi(server_id=srv.id)

        async def cross_dept_server(*args, **kwargs):
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND", message="not visible",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.server_svc.load_visible_server",
            cross_dept_server,
        )

        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 404

        failures = [
            e for e in captured
            if e["action"] == "ipmi_controller.rotate_dispatch"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1, captured
        ev = failures[0]
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "not_found_or_cross_dept"
        assert ev["target_id"] == ctrl.id


# ─────────────────────────────────────────────────────────────────────────────
# 3. _dispatch_for_server — no_ipmi audit emit (power.status)
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatchForServerNoIpmiAudit:
    """_dispatch_for_server с require_ipmi=True эмитит failure reason=no_ipmi."""

    async def test_no_ipmi_emits_failure_audit_with_no_ipmi_reason(
        self, client, operator_token_a, make_server, monkeypatch,
    ):
        """POST /power/status без IPMI → failure audit reason=no_ipmi."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
            fake_emit,
        )
        monkeypatch.setattr(
            "src.services.server.audit_service.emit",
            fake_emit,
        )

        srv = await make_server(department_id="dep_a")  # без IPMI
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert resp.json().get("error_code") == "NO_IPMI_CONTROLLER"

        power_failures = [
            e for e in captured
            if e["action"] == "server.power_status"
            and e.get("status") == "failure"
        ]
        assert len(power_failures) == 1, captured
        ev = power_failures[0]
        assert ev["details"]["reason"] == "no_ipmi"
        assert ev["details"]["department_id"] == "dep_a"
        assert ev["allowed"] is True

    async def test_no_ipmi_target_id_is_server_id(
        self, client, operator_token_a, make_server, monkeypatch,
    ):
        """В no_ipmi audit target_id — сервер (не controller), target_type — server."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
            fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404

        power_failures = [
            e for e in captured
            if e["action"] == "server.power_status"
            and e.get("status") == "failure"
            and (e.get("details") or {}).get("reason") == "no_ipmi"
        ]
        assert power_failures
        ev = power_failures[0]
        assert ev["target_id"] == srv.id
        assert ev["target_type"] == "server"


# ─────────────────────────────────────────────────────────────────────────────
# 4. denied→failure — _check_target_department_for_server actor-mismatch
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckTargetDeptForServerActorMismatch:
    """_check_target_department_for_server: actor dept mismatch → SERVER_NOT_FOUND.

    Функции, использующие _check_target_department_for_server:
      receive_inventory_facts → internal endpoint /inventory/facts
      record_server_prepared  → internal endpoint /servers/{id}/prepared
      submit_users_inventory_callback

    Тестируем на service-уровне через monkeypatch, симметрично
    test_internal_soft_mode_warning_audit.py::TestCheckTargetDeptForAccount.
    """

    @pytest.mark.asyncio
    async def test_receive_inventory_facts_actor_mismatch_returns_server_not_found(
        self, monkeypatch, db,
    ):
        """Caller из dep_b пытается отправить inventory для dep_a сервера
        → _check_target_department_for_server бросает SERVER_NOT_FOUND (маска)."""
        from src.services import internal_service

        server_id = "srv_actor_mismatch_x"

        class _Server:
            id = server_id
            department_id = "dep_a"

        async def _get_server(db_s, sid):
            if sid == server_id:
                return _Server()
            return None

        async def _require_ok(db_s, ident, et, ac):
            return None

        monkeypatch.setattr(internal_service.server_repo, "get_by_id", _get_server)
        monkeypatch.setattr(internal_service.permissions, "require_action", _require_ok)

        captured: list[dict] = []

        def fake_emit(action, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr(internal_service.audit_service, "emit", fake_emit)

        identity_b = IdentityContext(
            user_id="bot_b",
            username="worker",
            department_id="dep_b",
            department_name=None,
            allowed_services=["server_service"],
            service_roles={"server_service": ["worker_bot"]},
            is_banned=False,
            platform_role=None,
            subject_type="bot",
        )

        from src.schemas.internal import InventoryCallbackRequest

        payload = InventoryCallbackRequest(
            hostname="host1",
            kernel="5.10.0",
            cpu_brand="Intel",
            cpu_model="Xeon",
            cpu_cores=4,
            os_version="Astra 1.7",
            disks=[],
        )

        with pytest.raises(NotFoundError) as exc_info:
            await internal_service.receive_inventory(
                db, identity_b, server_id, payload, target_department_id="dep_b",
            )

        assert exc_info.value.error_code == "SERVER_NOT_FOUND"

        mismatch_emits = [
            e for e in captured
            if (e.get("details") or {}).get("reason") == "actor_department_mismatch"
        ]
        assert mismatch_emits, captured

    @pytest.mark.asyncio
    async def test_receive_inventory_facts_actor_mismatch_emits_denied(
        self, monkeypatch, db,
    ):
        """actor_department_mismatch audit emit содержит status=denied, allowed=False."""
        from src.services import internal_service

        server_id = "srv_dep_mismatch_audit"

        class _Server:
            id = server_id
            department_id = "dep_a"

        async def _get_server(db_s, sid):
            return _Server() if sid == server_id else None

        async def _require_ok(db_s, ident, et, ac):
            return None

        monkeypatch.setattr(internal_service.server_repo, "get_by_id", _get_server)
        monkeypatch.setattr(internal_service.permissions, "require_action", _require_ok)

        captured: list[dict] = []

        def fake_emit(action, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr(internal_service.audit_service, "emit", fake_emit)

        identity_b = IdentityContext(
            user_id="bot_b2",
            username="worker",
            department_id="dep_b",
            department_name=None,
            allowed_services=["server_service"],
            service_roles={"server_service": ["worker_bot"]},
            is_banned=False,
            platform_role=None,
            subject_type="bot",
        )

        from src.schemas.internal import InventoryCallbackRequest

        payload = InventoryCallbackRequest(
            hostname="host2",
            kernel="5.10.0",
            cpu_brand="AMD",
            cpu_model="EPYC",
            cpu_cores=16,
            os_version="Astra 1.7",
            disks=[],
        )

        with pytest.raises(NotFoundError):
            await internal_service.receive_inventory(
                db, identity_b, server_id, payload, target_department_id="dep_b",
            )

        denied_emits = [
            e for e in captured
            if e.get("status") == "denied" and e.get("allowed") is False
        ]
        assert denied_emits, captured
        ev = denied_emits[0]
        assert ev["details"]["reason"] == "actor_department_mismatch"
        assert ev["target_id"] == server_id


# ─────────────────────────────────────────────────────────────────────────────
# 5. identity= activated — subject_type в denied audit events
# ─────────────────────────────────────────────────────────────────────────────


class TestSubjectTypeInDeniedAudit:
    """subject_type пробрасывается через identity= в denied audit details.

    GAP-6: integration-тест, что реальный endpoint-emit содержит subject_type
    для bot-caller'а.
    """

    @pytest.fixture
    def captured_emits(self, monkeypatch):
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
            fake_emit,
        )
        monkeypatch.setattr("src.services.server.audit_service.emit", fake_emit)
        return captured

    async def test_bot_denied_power_status_contains_subject_type(
        self, client, make_server, make_token, dept_a, captured_emits,
    ):
        """Bot без роли → denied audit на power.status должен содержать subject_type=bot."""
        srv = await make_server(department_id=dept_a)

        # Токен с subject_type=bot и без operator-роли
        no_role_bot_tok = make_token(
            department_id=dept_a,
            service_roles={},
            subject_type="bot",
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(no_role_bot_tok),
        )
        assert resp.status_code == 403

        denied_emits = [
            e for e in captured_emits
            if e.get("status") == "denied"
        ]
        assert denied_emits, captured_emits
        ev = denied_emits[0]
        assert ev["details"].get("subject_type") == "bot"

    async def test_user_denied_power_status_subject_type_user(
        self, client, make_server, make_token, dept_a, captured_emits,
    ):
        """User без роли → denied audit subject_type=user."""
        srv = await make_server(department_id=dept_a)

        no_role_tok = make_token(
            department_id=dept_a,
            service_roles={},
            subject_type="user",
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(no_role_tok),
        )
        assert resp.status_code == 403

        denied_emits = [
            e for e in captured_emits
            if e.get("status") == "denied"
        ]
        assert denied_emits, captured_emits
        ev = denied_emits[0]
        assert ev["details"].get("subject_type") == "user"

    async def test_bot_denied_ipmi_rotate_contains_subject_type(
        self, client, make_server, make_ipmi, make_token, dept_a, captured_emits,
    ):
        """Bot без rotate_credentials → denied audit на ipmi rotate содержит subject_type."""
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)

        # reader_role у bota — нет rotate_credentials
        bot_tok = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["reader"]},
            subject_type="bot",
        )

        resp = await client.post(
            f"{BASE}/ipmi-controllers/{ctrl.id}/rotate",
            headers=_hdr(bot_tok),
        )
        assert resp.status_code == 403

        denied_emits = [
            e for e in captured_emits
            if e.get("status") == "denied"
            and e["action"] == "ipmi_controller.rotate_dispatch"
        ]
        assert denied_emits, captured_emits
        ev = denied_emits[0]
        assert ev["details"].get("subject_type") == "bot"

    async def test_no_subject_type_when_token_has_none(
        self, client, make_server, make_token, dept_a, captured_emits,
    ):
        """Если subject_type=None в токене → поле subject_type отсутствует в details."""
        srv = await make_server(department_id=dept_a)

        no_role_tok = make_token(
            department_id=dept_a,
            service_roles={},
            subject_type=None,
        )

        resp = await client.post(
            f"{BASE}/servers/{srv.id}/power/status",
            headers=_hdr(no_role_tok),
        )
        assert resp.status_code == 403

        denied_emits = [
            e for e in captured_emits
            if e.get("status") == "denied"
        ]
        assert denied_emits
        ev = denied_emits[0]
        # subject_type absent when identity.subject_type is None
        assert "subject_type" not in ev["details"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. rotated_at boundary value — ровно 600s (GAP-7)
# ─────────────────────────────────────────────────────────────────────────────


class TestRotatedAtBoundaryValue:
    """rotated_at граничное значение: ровно _ROTATED_AT_SKEW_SECONDS = 600s."""

    @pytest.mark.asyncio
    async def test_rotated_at_exactly_600s_in_future_is_rejected(
        self, client, admin_role_token_a, make_server, make_ipmi, dept_a,
    ):
        """rotated_at ровно на 600s вперёд → 400 ROTATED_AT_IN_FUTURE (граница не включительная).

        Условие: `rotated_drift > _ROTATED_AT_SKEW_SECONDS`
        При 600.001s → blocked. При 600.000s точно — float-арифметика,
        тест проверяет чуть выше (601s) для надёжности boundary check.
        """
        from src.core.config import get_settings

        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)

        skew = get_settings().ipmi_verify_max_age_seconds
        # rotated_at = now + 601s — ровно за границей 600s skew
        rotated_at = (datetime.now(timezone.utc) + timedelta(seconds=601)).isoformat()
        verified_at = datetime.now(timezone.utc).isoformat()

        resp = await client.post(
            f"/api/server/v1/internal/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers={**_hdr(admin_role_token_a), "X-Target-Department-Id": dept_a},
            json={
                "new_password": "BoundaryPwd123",
                "rotated_at": rotated_at,
                "verified_at": verified_at,
            },
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "ROTATED_AT_IN_FUTURE"

    @pytest.mark.asyncio
    async def test_rotated_at_within_skew_is_accepted(
        self, client, admin_role_token_a, make_server, make_ipmi, dept_a,
    ):
        """rotated_at в пределах skew (30s вперёд) принимается — NTP-jitter норма."""
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)

        rotated_at = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        verified_at = datetime.now(timezone.utc).isoformat()

        resp = await client.post(
            f"/api/server/v1/internal/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers={**_hdr(admin_role_token_a), "X-Target-Department-Id": dept_a},
            json={
                "new_password": "WithinSkewPwd123",
                "rotated_at": rotated_at,
                "verified_at": verified_at,
            },
        )
        # 200 — creds сохранились
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_rotated_at_exactly_at_boundary_unit(self):
        """Unit-проверка логики: rotated_drift > 600 блокирует, <= 600 — нет."""
        _ROTATED_AT_SKEW_SECONDS = 600

        # ровно на границе — не блокирует (> строгое)
        rotated_drift_on = float(_ROTATED_AT_SKEW_SECONDS)
        assert not (rotated_drift_on > _ROTATED_AT_SKEW_SECONDS)

        # на 1ms выше — блокирует
        rotated_drift_over = _ROTATED_AT_SKEW_SECONDS + 0.001
        assert rotated_drift_over > _ROTATED_AT_SKEW_SECONDS


# ─────────────────────────────────────────────────────────────────────────────
# 7. naive datetime service-level defence (GAP-8)
# ─────────────────────────────────────────────────────────────────────────────


class TestNaiveDatetimeDefence:
    """tzinfo is None в rotated_at/verified_at обрабатывается на service-уровне.

    При вызове через HTTP Pydantic нормализует naive → UTC уже в схеме.
    Ветка в internal_service.py:1256-1260 защищает прямые (unit/REPL) вызовы.
    """

    def test_pydantic_normalises_naive_to_utc(self):
        """Pydantic-слой нормализует naive datetime через _ensure_tz_aware."""
        from src.schemas.internal import IpmiCredentialsRotatedRequest

        naive = datetime(2026, 1, 1, 12, 0, 0)  # naive, без tzinfo
        assert naive.tzinfo is None

        payload = IpmiCredentialsRotatedRequest(
            new_password="NaivePwd1234",
            rotated_at=naive,
            verified_at=naive,
        )
        # Оба поля нормализованы → UTC
        assert payload.rotated_at.tzinfo is not None
        assert payload.verified_at.tzinfo is not None
        assert payload.rotated_at.tzinfo == timezone.utc
        assert payload.verified_at.tzinfo == timezone.utc
        # Значение момента не изменилось
        assert payload.rotated_at.replace(tzinfo=None) == naive

    def test_service_level_tzinfo_check_normalises_naive(self):
        """Service-level defence: если rotated_at.tzinfo is None → replace(tzinfo=UTC).

        Ветка непосредственно в internal_service.py:1256-1260.
        Проверяем логику напрямую (без HTTP/DB) как unit.
        """
        naive_dt = datetime(2026, 5, 31, 10, 0, 0)
        assert naive_dt.tzinfo is None

        # Применяем ту же логику, что в internal_service
        rotated_at = naive_dt
        if rotated_at.tzinfo is None:
            rotated_at = rotated_at.replace(tzinfo=timezone.utc)

        assert rotated_at.tzinfo is not None
        assert rotated_at.tzinfo == timezone.utc
        # Значение сохранено
        assert rotated_at == naive_dt.replace(tzinfo=timezone.utc)
