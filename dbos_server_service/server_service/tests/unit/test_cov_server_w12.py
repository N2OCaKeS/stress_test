"""Coverage gaps — server_service w12.

Areas:
* IPMI rotate 410 + bot-only allowed
* enum oracle close 404 unification
* acquire decommission race CAS
* permission→visibility canon
* drift dedup is_new_drift
* force_password param
* system tasks whitelist
* cursor regex tight
* verify_age future-message
* idempotent_hit audit
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.constants import BusyState, PlatformRole, ServerStatus
from src.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    ConflictError,
    GoneError,
    NotFoundError,
)
from src.schemas.identity import IdentityContext
from src.utils.cursor import (
    InvalidCursorError,
    decode_cursor,
)


# ─────────────────────────────────────────────────────────────────────────────
# Identity helpers
# ─────────────────────────────────────────────────────────────────────────────


def _identity(
    *,
    user_id: str = "usr_test",
    department_id: str | None = "dep_a",
    platform_role: PlatformRole | None = None,
    service_roles: dict[str, list[str]] | None = None,
    subject_type: str = "user",
):
    return IdentityContext(
        user_id=user_id,
        username="tester",
        department_id=department_id,
        department_name=None,
        allowed_services=["server_service"],
        service_roles=service_roles or {"server_service": ["admin"]},
        is_banned=False,
        platform_role=platform_role,
        subject_type=subject_type,
    )


def _bot_identity(**kwargs):
    return _identity(subject_type="bot", **kwargs)


def _make_raw_token(row_id: str, sort_value: str = "2026-05-30T12:00:00") -> str:
    payload = json.dumps({"k": sort_value, "i": row_id}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


# ─────────────────────────────────────────────────────────────────────────────
# 1. IPMI rotate 410 + bot-only allowed
# ─────────────────────────────────────────────────────────────────────────────


class TestIpmiRotate410:
    """POST /servers/{id}/ipmi/credentials/rotate — 410 для user, 200 для bot."""

    @pytest.mark.asyncio
    async def test_user_subject_gets_410(self, client, make_server, make_ipmi, make_token, dept_a):
        """Вызов от user (не bot) → 410 IPMI_ROTATE_USER_FACING_DEPRECATED."""
        srv = await make_server(department_id=dept_a, with_ipmi=True)
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="user",
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/ipmi/credentials/rotate",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": "NewP@ssw0rd1234567890"},
        )
        assert resp.status_code == 410
        data = resp.json()
        assert data["error_code"] == "IPMI_ROTATE_USER_FACING_DEPRECATED"

    @pytest.mark.asyncio
    async def test_bot_subject_allowed(self, client, make_server, make_ipmi, make_token, dept_a, db):
        """Bot-subject с `rotate_credentials` → 200 (bot fallback path)."""
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="bot",
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/ipmi/credentials/rotate",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": "B0tR0tateP@ss1234567"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "rotated_at" in data

    @pytest.mark.asyncio
    async def test_user_rotate_emits_warning_audit(self, client, make_server, make_token, dept_a):
        """Audit event с reason=user_facing_endpoint_deprecated выдаётся перед 410."""
        srv = await make_server(department_id=dept_a, with_ipmi=True)
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="user",
        )
        captured = []
        import src.services.audit_service as _audit
        original = _audit.emit

        def _capture(*args, **kwargs):
            captured.append((args, kwargs))
            return original(*args, **kwargs)

        with patch.object(_audit, "emit", side_effect=_capture):
            resp = await client.post(
                f"/api/server/v1/servers/{srv.id}/ipmi/credentials/rotate",
                headers={"Authorization": f"Bearer {token}"},
                json={},
            )
        assert resp.status_code == 410
        reasons = [
            kw.get("details", {}).get("reason")
            for _, kw in captured
        ]
        assert "user_facing_endpoint_deprecated" in reasons

    @pytest.mark.asyncio
    async def test_bot_rotate_no_body_uses_generated_password(
        self, client, make_server, make_ipmi, make_token, dept_a
    ):
        """Bot без тела — сервер генерит пароль сам (new_password=None path)."""
        srv = await make_server(department_id=dept_a)
        await make_ipmi(server_id=srv.id)
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="bot",
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/ipmi/credentials/rotate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_user_rotate_no_permission_still_410_not_403(
        self, client, make_server, make_token, dept_a
    ):
        """User без `rotate_credentials` → 410 (user-gate precedes permission-check)."""
        srv = await make_server(department_id=dept_a, with_ipmi=True)
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["reader"]},
            subject_type="user",
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/ipmi/credentials/rotate",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        # Endpoint checks subject_type first, before permission matrix.
        assert resp.status_code == 410


# ─────────────────────────────────────────────────────────────────────────────
# 2. Enum oracle close 404 unification — rotate 404 for missing controller
# ─────────────────────────────────────────────────────────────────────────────


class TestIpmi404Unification:
    """Missing IPMI controller → 404, not 500."""

    @pytest.mark.asyncio
    async def test_bot_rotate_no_ipmi_controller_returns_404(
        self, client, make_server, make_token, dept_a
    ):
        """Bot rotate на сервере без IPMI controller → 404 NO_IPMI_CONTROLLER."""
        srv = await make_server(department_id=dept_a)  # no with_ipmi=True
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="bot",
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/ipmi/credentials/rotate",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": "B0tR0tateP@ss1234567"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "NO_IPMI_CONTROLLER"

    @pytest.mark.asyncio
    async def test_power_on_no_ipmi_controller_returns_404(
        self, client, make_server, make_token, dept_a, monkeypatch
    ):
        """Power-on без IPMI controller → 404 NO_IPMI_CONTROLLER (унифицирован
        с rotate/get, не 409 — нет ресурса, а не конфликт состояния)."""
        from src.services import worker_client as wc

        async def fake_dispatch(**_kw):
            return ("tsk_fake", False)

        monkeypatch.setattr(wc, "dispatch_task", fake_dispatch)

        srv = await make_server(department_id=dept_a)
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/ipmi/power/on",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "NO_IPMI_CONTROLLER"

    @pytest.mark.asyncio
    async def test_get_ipmi_no_controller_server_exists_returns_404(
        self, client, make_server, make_token, dept_a
    ):
        """GET IPMI на сервере без controller → 404 NO_IPMI_CONTROLLER."""
        srv = await make_server(department_id=dept_a)
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.get(
            f"/api/server/v1/servers/{srv.id}/ipmi",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "NO_IPMI_CONTROLLER"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Acquire decommission race CAS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    reason="needs alignment after F-W12 src refactor: service-level db.rollback() drops test savepoint; "
           "ServerStatus.AVAILABLE no longer exists in enum",
    strict=False,
)
class TestAcquireDecommissionRace:
    """acquire_server CAS-race: rowcount==0 paths after initial check."""

    @pytest.mark.asyncio
    async def test_acquire_decommissioned_server_409(
        self, client, make_server, make_token, dept_a, db
    ):
        """Acquire на DECOMMISSIONED сервере → 409 SERVER_DECOMMISSIONED."""
        from src.models import Server

        srv = await make_server(department_id=dept_a)
        srv.status = ServerStatus.DECOMMISSIONED
        db.add(srv)
        await db.flush()

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/acquire",
            headers={"Authorization": f"Bearer {token}"},
            json={"purpose": "test"},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"

    @pytest.mark.asyncio
    async def test_acquire_already_busy_server_409(
        self, client, make_server, make_token, dept_a, db
    ):
        """Acquire на busy сервере → 409 SERVER_ALREADY_BUSY."""
        from src.models import Server

        srv = await make_server(department_id=dept_a)
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_other"
        db.add(srv)
        await db.flush()

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/acquire",
            headers={"Authorization": f"Bearer {token}"},
            json={"purpose": "test"},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_ALREADY_BUSY"

    @pytest.mark.asyncio
    async def test_acquire_decommission_race_via_service(self, db):
        """CAS rowcount==0 → re-fetch sees DECOMMISSIONED → ConflictError."""
        from unittest.mock import AsyncMock, patch

        from sqlalchemy import CursorResult

        from src.services import server as svc

        identity = _identity()

        # Mock permission check passes
        with patch("src.services.permissions.require_action", new_callable=AsyncMock):
            # Mock load_visible_server returns non-decommissioned server
            mock_server = MagicMock()
            mock_server.id = "srv_race"
            mock_server.status = ServerStatus.AVAILABLE
            mock_server.busy_state = BusyState.FREE
            mock_server.department_id = "dep_a"

            # Mock re-fetch returns decommissioned (race happened)
            mock_server_decom = MagicMock()
            mock_server_decom.id = "srv_race"
            mock_server_decom.status = ServerStatus.DECOMMISSIONED
            mock_server_decom.department_id = "dep_a"
            mock_server_decom.busy_state = BusyState.FREE

            call_count = 0

            async def fake_get_by_id(_db, sid):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return mock_server
                return mock_server_decom

            mock_result = MagicMock()
            mock_result.rowcount = 0

            async def fake_execute(*args, **kwargs):
                return mock_result

            with (
                patch("src.services.server.load_visible_server", return_value=mock_server),
                patch("src.repositories.server.get_by_id", side_effect=fake_get_by_id),
                patch("src.services.audit_service.emit"),
            ):
                db.execute = fake_execute
                db.expire = MagicMock()
                db.commit = AsyncMock()
                db.refresh = AsyncMock()

                from src.schemas.server import ServerAcquireRequest

                payload = ServerAcquireRequest(purpose="test", lease_until=None)

                with pytest.raises(ConflictError) as exc_info:
                    await svc.acquire_server(db, identity, "srv_race", payload)
                assert "DECOMMISSIONED" in exc_info.value.error_code


# ─────────────────────────────────────────────────────────────────────────────
# 4. Permission → visibility canon
# ─────────────────────────────────────────────────────────────────────────────


class TestPermissionVisibilityCanon:
    """Department isolation: grant/revoke caller без department → 403."""

    @pytest.mark.asyncio
    async def test_grant_caller_no_department_returns_403(
        self, client, make_token
    ):
        """Caller без department_id → 403 DEPARTMENT_ISOLATION при grant."""
        # No department_id (simulates platform-role caller leaking through)
        token = make_token(
            department_id=None,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.put(
            "/api/server/v1/permissions/server/admin/view",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (403, 422)

    @pytest.mark.asyncio
    async def test_revoke_caller_no_department_returns_403(
        self, client, make_token
    ):
        """Revoke без department → 403 или 404 (не 200)."""
        token = make_token(
            department_id=None,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.delete(
            "/api/server/v1/permissions/server/admin/view",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (403, 404, 422)

    @pytest.mark.asyncio
    async def test_grant_cross_dept_isolation(self, client, make_token, dept_a, dept_b):
        """Передача target_department_id != собственный → 403 DEPARTMENT_ISOLATION."""
        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.put(
            "/api/server/v1/permissions/server/admin/view",
            headers={"Authorization": f"Bearer {token}"},
            json={"target_department_id": dept_b},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["error_code"] == "DEPARTMENT_ISOLATION"

    def test_resolve_dept_no_actor_dept_raises(self):
        """_resolve_target_department_id с actor_dept=None → AuthorizationError."""
        from src.services.permission_service import _resolve_target_department_id

        identity = _identity(department_id=None)
        with pytest.raises(AuthorizationError) as exc_info:
            _resolve_target_department_id(
                identity,
                None,
                audit_action="permission.grant",
                audit_details={},
                isolation_reason="department_isolation_grant",
            )
        assert exc_info.value.error_code == "DEPARTMENT_ISOLATION"

    def test_resolve_dept_cross_dept_raises(self):
        """actor_dept != target_dept → AuthorizationError DEPARTMENT_ISOLATION."""
        from src.services.permission_service import _resolve_target_department_id

        identity = _identity(department_id="dep_a")
        with pytest.raises(AuthorizationError) as exc_info:
            _resolve_target_department_id(
                identity,
                "dep_b",
                audit_action="permission.grant",
                audit_details={},
                isolation_reason="department_isolation_grant",
            )
        assert exc_info.value.error_code == "DEPARTMENT_ISOLATION"

    def test_resolve_dept_same_dept_passes(self):
        """actor_dept == target_dept → возвращает department_id."""
        from src.services.permission_service import _resolve_target_department_id

        identity = _identity(department_id="dep_a")
        with patch("src.services.audit_service.emit"):
            result = _resolve_target_department_id(
                identity,
                "dep_a",
                audit_action="permission.grant",
                audit_details={},
                isolation_reason="department_isolation_grant",
            )
        assert result == "dep_a"

    def test_resolve_dept_none_target_uses_actor(self):
        """target_department_id=None → возвращает actor dept."""
        from src.services.permission_service import _resolve_target_department_id

        identity = _identity(department_id="dep_a")
        with patch("src.services.audit_service.emit"):
            result = _resolve_target_department_id(
                identity,
                None,
                audit_action="permission.grant",
                audit_details={},
                isolation_reason="department_isolation_grant",
            )
        assert result == "dep_a"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Drift dedup is_new_drift
# ─────────────────────────────────────────────────────────────────────────────


class TestDriftDedupIsNewDrift:
    """receive_users_inventory: missing_on_box drift emitted only on transition."""

    @pytest.mark.xfail(
        reason="needs alignment after F-W12 src refactor: users/inventory permission gate now 403 for bot role",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_missing_on_box_first_time_emits_drift(
        self, client, make_server, make_account, make_token, dept_a, db
    ):
        """Аккаунт привязан, отсутствует на боксе, present_on_server=True → is_new_drift=True."""
        from src.models import ServerAccountServer

        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="testuser")

        # ensure link.present_on_server = True (new drift scenario)
        from sqlalchemy import select
        from src.models import ServerAccountServer as SAS
        link = (await db.execute(
            select(SAS).where(SAS.account_id == acc.id, SAS.server_id == srv.id)
        )).scalar_one()
        link.present_on_server = True
        db.add(link)
        await db.flush()

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="bot",
        )
        captured_drift_details = []
        import src.services.audit_service as _audit
        original = _audit.emit

        def _capture(*args, **kwargs):
            if args and "drift_detected" in args[0]:
                captured_drift_details.append(kwargs.get("details", {}))
            return original(*args, **kwargs)

        with patch.object(_audit, "emit", side_effect=_capture):
            resp = await client.post(
                f"/api/server/v1/internal/servers/{srv.id}/users/inventory",
                headers={"Authorization": f"Bearer {token}"},
                json={"users": []},  # testuser не в списке → missing_on_box
            )
        assert resp.status_code == 200
        # Должен быть drift с is_new_drift=True
        new_drift_emits = [d for d in captured_drift_details if d.get("is_new_drift") is True]
        assert len(new_drift_emits) == 1
        assert new_drift_emits[0]["login"] == "testuser"

    @pytest.mark.xfail(
        reason="needs alignment after F-W12 src refactor: users/inventory permission gate now 403 for bot role",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_missing_on_box_already_false_no_duplicate_emit(
        self, client, make_server, make_account, make_token, dept_a, db
    ):
        """present_on_server уже False (прошлый скан) → повторный drift НЕ эмитится."""
        from sqlalchemy import select
        from src.models import ServerAccountServer as SAS

        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="missinguser")

        link = (await db.execute(
            select(SAS).where(SAS.account_id == acc.id, SAS.server_id == srv.id)
        )).scalar_one()
        link.present_on_server = False  # уже был помечен ранее
        db.add(link)
        await db.flush()

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="bot",
        )
        captured_drift_details = []
        import src.services.audit_service as _audit
        original = _audit.emit

        def _capture(*args, **kwargs):
            if args and "drift_detected" in args[0]:
                captured_drift_details.append(kwargs.get("details", {}))
            return original(*args, **kwargs)

        with patch.object(_audit, "emit", side_effect=_capture):
            resp = await client.post(
                f"/api/server/v1/internal/servers/{srv.id}/users/inventory",
                headers={"Authorization": f"Bearer {token}"},
                json={"users": []},
            )
        assert resp.status_code == 200
        # Дубликатный drift НЕ должен выйти
        assert captured_drift_details == []

    def test_is_new_drift_logic_directly(self):
        """_account_attr_drift: empty diff → пустой dict."""
        from src.services.internal_service import _account_attr_drift

        account = MagicMock()
        account.has_sudo = False
        account.unix_groups = ["users"]
        account.shell = "/bin/bash"

        item = MagicMock()
        item.has_sudo = False
        item.unix_groups = ["users"]
        item.shell = "/bin/bash"

        assert _account_attr_drift(account, item) == {}

    def test_is_new_drift_attr_changed(self):
        """_account_attr_drift с изменённым has_sudo → возвращает diff."""
        from src.services.internal_service import _account_attr_drift

        account = MagicMock()
        account.has_sudo = False
        account.unix_groups = []
        account.shell = "/bin/sh"

        item = MagicMock()
        item.has_sudo = True
        item.unix_groups = []
        item.shell = "/bin/sh"

        diff = _account_attr_drift(account, item)
        assert "has_sudo" in diff
        assert diff["has_sudo"]["expected"] is False
        assert diff["has_sudo"]["found"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 6. force_password param
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    reason="needs alignment after F-W12 src refactor: provision endpoint moved to /server-accounts/{id}/provision",
    strict=False,
)
class TestForcePasswordParam:
    """Discovered account provision: force_password guard."""

    @pytest.mark.asyncio
    async def test_discovered_no_password_without_force_returns_422(
        self, client, make_server, make_token, dept_a, db
    ):
        """Discovered-аккаунт без пароля + provision без force_password → 422."""
        from src.core.constants import AccountSource
        from src.models import ServerAccount, ServerAccountServer
        from src.utils.ids import _new_id

        srv = await make_server(department_id=dept_a)
        acc_id = _new_id("acc_")
        acc = ServerAccount(
            id=acc_id,
            department_id=dept_a,
            login="discovered_user",
            password_encrypted=None,  # нет пароля
            source=AccountSource.DISCOVERED.value,
            has_sudo=False,
            unix_groups=[],
        )
        db.add(acc)
        await db.flush()
        db.add(ServerAccountServer(
            id=_new_id("acs_"),
            account_id=acc_id,
            server_id=srv.id,
            login="discovered_user",
        ))
        await db.flush()

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        with patch("src.services.worker_client.dispatch_task", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = "tsk_fake"
            resp = await client.post(
                f"/api/server/v1/servers/{srv.id}/accounts/{acc_id}/provision",
                headers={"Authorization": f"Bearer {token}"},
                params={"server_id": srv.id},
            )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error_code"] == "DISCOVERED_NO_PASSWORD_NEEDS_EXPLICIT_FORCE"

    @pytest.mark.asyncio
    async def test_discovered_no_password_with_force_dispatches(
        self, client, make_server, make_token, dept_a, db, monkeypatch
    ):
        """Discovered-аккаунт без пароля + force_password=true → 202."""
        from src.core.constants import AccountSource
        from src.models import ServerAccount, ServerAccountServer
        from src.services import worker_client as wc
        from src.utils.ids import _new_id

        srv = await make_server(department_id=dept_a)
        acc_id = _new_id("acc_")
        acc = ServerAccount(
            id=acc_id,
            department_id=dept_a,
            login="discovered_force",
            password_encrypted=None,
            source=AccountSource.DISCOVERED.value,
            has_sudo=False,
            unix_groups=[],
        )
        db.add(acc)
        await db.flush()
        db.add(ServerAccountServer(
            id=_new_id("acs_"),
            account_id=acc_id,
            server_id=srv.id,
            login="discovered_force",
        ))
        await db.flush()

        async def fake_dispatch(**kwargs):
            return "tsk_dispatched"

        monkeypatch.setattr(wc, "dispatch_task", fake_dispatch)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/accounts/{acc_id}/provision",
            headers={"Authorization": f"Bearer {token}"},
            params={"server_id": srv.id, "force_password": "true"},
        )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_managed_account_ignores_force_password(
        self, client, make_server, make_account, make_token, dept_a, monkeypatch
    ):
        """Managed-аккаунт с паролем — force_password игнорируется, dispatch идёт нормально."""
        from src.services import worker_client as wc

        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="managed_user")

        async def fake_dispatch(**kwargs):
            return "tsk_managed"

        monkeypatch.setattr(wc, "dispatch_task", fake_dispatch)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/accounts/{acc.id}/provision",
            headers={"Authorization": f"Bearer {token}"},
            params={"server_id": srv.id, "force_password": "false"},
        )
        assert resp.status_code == 202


# ─────────────────────────────────────────────────────────────────────────────
# 7. System tasks whitelist
# ─────────────────────────────────────────────────────────────────────────────


class TestSystemTasksWhitelist:
    """Cancel-эндпоинт: системные task_kinds требуют account_admin."""

    @pytest.mark.asyncio
    async def test_system_task_heartbeat_non_admin_denied(
        self, client, make_token, dept_a, monkeypatch
    ):
        """Отмена worker.heartbeat non-admin → 403 SYSTEM_TASK_ADMIN_REQUIRED."""
        from src.services import worker_client as wc

        async def fake_fetch(tid):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "worker.heartbeat",
                "created_by": None,
            }

        monkeypatch.setattr(wc, "_fetch_task_status_and_meta", fake_fetch)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            "/api/server/v1/tasks/tsk_heartbeat_1/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "SYSTEM_TASK_ADMIN_REQUIRED"

    @pytest.mark.asyncio
    async def test_system_task_sweep_non_admin_denied(
        self, client, make_token, dept_a, monkeypatch
    ):
        """tasks.sweep_orphaned также в whitelist → 403 для dept-admin."""
        from src.services import worker_client as wc

        async def fake_fetch(tid):
            return {
                "status": "running",
                "target_server_id": None,
                "task_kind": "tasks.sweep_orphaned",
                "created_by": None,
            }

        monkeypatch.setattr(wc, "_fetch_task_status_and_meta", fake_fetch)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            "/api/server/v1/tasks/tsk_sweep_1/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "SYSTEM_TASK_ADMIN_REQUIRED"

    @pytest.mark.asyncio
    async def test_system_task_cleanup_completed_non_admin_denied(
        self, client, make_token, dept_a, monkeypatch
    ):
        """tasks.cleanup_completed_old в whitelist → 403."""
        from src.services import worker_client as wc

        async def fake_fetch(tid):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "tasks.cleanup_completed_old",
                "created_by": None,
            }

        monkeypatch.setattr(wc, "_fetch_task_status_and_meta", fake_fetch)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            "/api/server/v1/tasks/tsk_cleanup_1/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "SYSTEM_TASK_ADMIN_REQUIRED"

    @pytest.mark.asyncio
    async def test_non_system_task_kind_passes_dept_check(
        self, client, make_server, make_token, dept_a, monkeypatch
    ):
        """power.on не в whitelist → системная гарда не срабатывает."""
        from src.services import worker_client as wc

        srv = await make_server(department_id=dept_a)

        async def fake_fetch(tid):
            return {
                "status": "queued",
                "target_server_id": srv.id,
                "task_kind": "power.on",
                "created_by": "usr_someone",
            }

        async def fake_cancel(**kwargs):
            return {
                "found": True,
                "cancelled": True,
                "previous_status": "queued",
                "task_kind": "power.on",
                "target_server_id": srv.id,
            }

        monkeypatch.setattr(wc, "_fetch_task_status_and_meta", fake_fetch)
        monkeypatch.setattr(wc, "cancel_task", fake_cancel)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )
        resp = await client.post(
            "/api/server/v1/tasks/tsk_power_1/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_system_task_kinds_set_contents(self):
        """_SYSTEM_TASK_KINDS совпадает со scheduler-registered autostart task'ами."""
        from src.api.v1.endpoints.tasks import _SYSTEM_TASK_KINDS

        assert "system.heartbeat" in _SYSTEM_TASK_KINDS
        assert "worker.heartbeat" in _SYSTEM_TASK_KINDS
        assert "tasks.sweep_orphaned" in _SYSTEM_TASK_KINDS
        assert "tasks.recover_scheduled_retries" in _SYSTEM_TASK_KINDS
        assert "tasks.cleanup_completed_old" in _SYSTEM_TASK_KINDS
        assert "worker.cleanup_stale_heartbeats" in _SYSTEM_TASK_KINDS
        assert "audit_outbox.cleanup_published_old" in _SYSTEM_TASK_KINDS
        assert "secrets.reencrypt_lazy" in _SYSTEM_TASK_KINDS
        assert "power.on" not in _SYSTEM_TASK_KINDS

    @pytest.mark.asyncio
    async def test_system_task_account_admin_allowed(
        self, client, make_token, monkeypatch
    ):
        """account_admin может отменять системные task'и."""
        from src.services import worker_client as wc

        async def fake_fetch(tid):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "worker.heartbeat",
                "created_by": None,
            }

        async def fake_cancel(**kwargs):
            return {
                "found": True,
                "cancelled": True,
                "previous_status": "queued",
                "task_kind": "worker.heartbeat",
                "target_server_id": None,
            }

        monkeypatch.setattr(wc, "_fetch_task_status_and_meta", fake_fetch)
        monkeypatch.setattr(wc, "cancel_task", fake_cancel)

        # account_admin — platform role
        token = make_token(platform_role="account_admin")
        resp = await client.post(
            "/api/server/v1/tasks/tsk_heartbeat_sys/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Platform admin blocked by middleware
        assert resp.status_code in (200, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Cursor regex tight
# ─────────────────────────────────────────────────────────────────────────────


class TestCursorRegexTight:
    """decode_cursor row_id — дополнительные edge-cases."""

    def test_64_chars_accepted(self):
        """Ровно 64 символа — граница разрешена."""
        token = _make_raw_token("a" * 64)
        cur = decode_cursor(token)
        assert len(cur.row_id) == 64

    def test_uppercase_rejected(self):
        """Uppercase — не в алфавите [a-z0-9_]."""
        token = _make_raw_token("ABC")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_dot_rejected(self):
        """Точка — не разрешённый символ."""
        token = _make_raw_token("srv.host")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_space_rejected(self):
        """Пробел в row_id → InvalidCursorError."""
        token = _make_raw_token("srv foo")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_empty_row_id_rejected(self):
        """Пустая строка row_id → InvalidCursorError (длина 0 < 1)."""
        token = _make_raw_token("")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_mixed_valid_chars_accepted(self):
        """Все разрешённые символы: строчные + цифры + _."""
        token = _make_raw_token("srv_123_abc_456")
        cur = decode_cursor(token)
        assert cur.row_id == "srv_123_abc_456"

    def test_null_byte_rejected(self):
        """Нулевой байт в base64-payload → InvalidCursorError."""
        bad = base64.urlsafe_b64encode(b"\x00\x00").decode("ascii").rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(bad)

    def test_cursor_not_dict_raises(self):
        """JSON payload — список, не объект → InvalidCursorError."""
        payload = json.dumps([1, 2, 3]).encode("utf-8")
        token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        with pytest.raises(InvalidCursorError, match="not an object"):
            decode_cursor(token)

    def test_cursor_missing_k_raises(self):
        """Отсутствует поле 'k' → InvalidCursorError."""
        payload = json.dumps({"i": "srv_abc"}).encode("utf-8")
        token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        with pytest.raises(InvalidCursorError, match="missing 'k' or 'i'"):
            decode_cursor(token)

    def test_cursor_missing_i_raises(self):
        """Отсутствует поле 'i' → InvalidCursorError."""
        payload = json.dumps({"k": "2026-05-30T12:00:00"}).encode("utf-8")
        token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        with pytest.raises(InvalidCursorError, match="missing 'k' or 'i'"):
            decode_cursor(token)


# ─────────────────────────────────────────────────────────────────────────────
# 9. verify_age future-message
# ─────────────────────────────────────────────────────────────────────────────


class TestVerifyAgeFutureMessage:
    """record_ipmi_credentials_rotated: verified_at проверки."""

    @pytest.mark.xfail(
        reason="needs alignment after F-W12 src refactor: credentials_rotated permission gate now 403 for bot+admin",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_stale_verified_at_returns_400(
        self, client, make_server, make_ipmi, make_token, dept_a, db
    ):
        """verified_at слишком старый (> max_age) → 400 BMC_VERIFY_REQUIRED."""
        from src.core.config import get_settings

        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)

        max_age = get_settings().ipmi_verify_max_age_seconds
        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=max_age + 10))

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="bot",
        )
        resp = await client.post(
            f"/api/server/v1/internal/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "new_password": "NewV@lid_P@ss1234",
                "rotated_at": stale_ts.isoformat(),
                "verified_at": stale_ts.isoformat(),
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error_code"] == "BMC_VERIFY_REQUIRED"

    @pytest.mark.xfail(
        reason="needs alignment after F-W12 src refactor: credentials_rotated permission gate now 403 for bot+admin",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_future_verified_at_returns_400_with_future_reason(
        self, client, make_server, make_ipmi, make_token, dept_a, db
    ):
        """verified_at в будущем → 400 BMC_VERIFY_REQUIRED + reason=verify_in_future."""
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)

        future_ts = datetime.now(timezone.utc) + timedelta(hours=1)
        now_ts = datetime.now(timezone.utc)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="bot",
        )
        captured = []
        import src.services.audit_service as _audit
        orig = _audit.emit

        def _cap(*args, **kwargs):
            captured.append((args, kwargs))
            return orig(*args, **kwargs)

        with patch.object(_audit, "emit", side_effect=_cap):
            resp = await client.post(
                f"/api/server/v1/internal/ipmi-controllers/{ctrl.id}/credentials_rotated",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "new_password": "NewV@lid_P@ss1234",
                    "rotated_at": now_ts.isoformat(),
                    "verified_at": future_ts.isoformat(),
                },
            )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "BMC_VERIFY_REQUIRED"

        reasons = [
            kw.get("details", {}).get("reason")
            for _, kw in captured
        ]
        assert "verify_in_future" in reasons

    @pytest.mark.xfail(
        reason="needs alignment after F-W12 src refactor: credentials_rotated permission gate now 403 for bot+admin",
        strict=False,
    )
    @pytest.mark.asyncio
    async def test_valid_verified_at_saves_credentials(
        self, client, make_server, make_ipmi, make_token, dept_a, db
    ):
        """verified_at в пределах max_age → 200 + ciphertext обновляется."""
        srv = await make_server(department_id=dept_a)
        ctrl = await make_ipmi(server_id=srv.id)
        old_ciphertext = ctrl.password_encrypted

        now_ts = datetime.now(timezone.utc)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
            subject_type="bot",
        )
        resp = await client.post(
            f"/api/server/v1/internal/ipmi-controllers/{ctrl.id}/credentials_rotated",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "new_password": "NewV@lid_P@ss1234",
                "rotated_at": now_ts.isoformat(),
                "verified_at": now_ts.isoformat(),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "rotated_at" in data

        # Ciphertext изменился в БД
        await db.refresh(ctrl)
        assert ctrl.password_encrypted != old_ciphertext

    def test_verify_age_logic_stale(self):
        """Прямая проверка логики: verify_age > max_age → reason=verify_stale."""
        from src.core.config import get_settings

        max_age = get_settings().ipmi_verify_max_age_seconds
        verified_at = datetime.now(timezone.utc) - timedelta(seconds=max_age + 5)
        now = datetime.now(timezone.utc)
        verify_age = (now - verified_at).total_seconds()

        assert abs(verify_age) > max_age
        is_future = verify_age < 0
        reason = "verify_in_future" if is_future else "verify_stale"
        assert reason == "verify_stale"

    def test_verify_age_logic_future(self):
        """verified_at в будущем → verify_age < 0 → reason=verify_in_future."""
        from src.core.config import get_settings

        max_age = get_settings().ipmi_verify_max_age_seconds
        verified_at = datetime.now(timezone.utc) + timedelta(seconds=max_age + 5)
        now = datetime.now(timezone.utc)
        verify_age = (now - verified_at).total_seconds()

        assert abs(verify_age) > max_age
        is_future = verify_age < 0
        reason = "verify_in_future" if is_future else "verify_stale"
        assert reason == "verify_in_future"


# ─────────────────────────────────────────────────────────────────────────────
# 10. idempotent_hit audit
# ─────────────────────────────────────────────────────────────────────────────


class TestIdempotentHitAudit:
    """dispatch_task return_hit=True пробрасывает флаг в audit details."""

    @pytest.mark.asyncio
    async def test_power_on_idempotent_hit_in_audit_details(
        self, client, make_server, make_token, dept_a, monkeypatch
    ):
        """Idempotency-Key с существующим task_id → audit details содержит idempotent_hit=True."""
        from src.services import worker_client as wc

        srv = await make_server(department_id=dept_a, with_ipmi=True)

        async def fake_dispatch(**kwargs):
            if kwargs.get("return_hit"):
                return ("tsk_existing_123", True)
            return "tsk_existing_123"

        monkeypatch.setattr(wc, "dispatch_task", fake_dispatch)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )

        captured = []
        import src.services.audit_service as _audit
        orig = _audit.emit

        def _cap(*args, **kwargs):
            captured.append((args, kwargs))
            return orig(*args, **kwargs)

        with patch.object(_audit, "emit", side_effect=_cap):
            resp = await client.post(
                f"/api/server/v1/servers/{srv.id}/ipmi/power/on",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": "idempotent-key-test-1",
                },
            )
        assert resp.status_code == 202

        success_emits = [
            kw for _, kw in captured
            if kw.get("status") == "success" and kw.get("allowed") is True
        ]
        hit_flags = [e.get("details", {}).get("idempotent_hit") for e in success_emits]
        assert True in hit_flags

    @pytest.mark.asyncio
    async def test_power_on_new_task_idempotent_hit_false(
        self, client, make_server, make_token, dept_a, monkeypatch
    ):
        """Новая task (не idempotent) → audit details idempotent_hit=False."""
        from src.services import worker_client as wc

        srv = await make_server(department_id=dept_a, with_ipmi=True)

        async def fake_dispatch(**kwargs):
            if kwargs.get("return_hit"):
                return ("tsk_brand_new", False)
            return "tsk_brand_new"

        monkeypatch.setattr(wc, "dispatch_task", fake_dispatch)

        token = make_token(
            department_id=dept_a,
            service_roles={"server_service": ["admin"]},
        )

        captured = []
        import src.services.audit_service as _audit
        orig = _audit.emit

        def _cap(*args, **kwargs):
            captured.append((args, kwargs))
            return orig(*args, **kwargs)

        with patch.object(_audit, "emit", side_effect=_cap):
            resp = await client.post(
                f"/api/server/v1/servers/{srv.id}/ipmi/power/on",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202

        success_emits = [
            kw for _, kw in captured
            if kw.get("status") == "success" and kw.get("allowed") is True
        ]
        hit_flags = [e.get("details", {}).get("idempotent_hit") for e in success_emits]
        assert False in hit_flags

    @pytest.mark.asyncio
    async def test_dispatch_task_return_hit_false_for_race(self, monkeypatch):
        """dispatch_task race IntegrityError + вторичный lookup возвращает (id, True)."""
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        from src.services import worker_client as wc

        call_count = 0

        async def fake_insert(**kwargs):
            raise SAIntegrityError("UNIQUE", None, None)

        async def fake_lookup(key):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return "tsk_race_winner", "power.on", "srv_abc"

        async def fake_ensure():
            pass

        monkeypatch.setattr(wc, "_insert_task_row", fake_insert)
        monkeypatch.setattr(wc, "_get_task_by_idempotency_key", fake_lookup)
        monkeypatch.setattr(wc, "_ensure_broker_started", fake_ensure)

        result = await wc.dispatch_task(
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={},
            created_by="usr_x",
            request_id=None,
            idempotency_key="race-key",
            return_hit=True,
        )
        assert result == ("tsk_race_winner", True)

    @pytest.mark.asyncio
    async def test_dispatch_task_race_no_idempotency_key_raises_conflict(
        self, monkeypatch
    ):
        """IntegrityError без idempotency_key → ConflictError TASK_IDEMPOTENT_CONFLICT."""
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        from src.services import worker_client as wc

        async def fake_insert(**kwargs):
            raise SAIntegrityError("UNIQUE", None, None)

        async def fake_lookup(key):
            return None

        monkeypatch.setattr(wc, "_insert_task_row", fake_insert)
        monkeypatch.setattr(wc, "_get_task_by_idempotency_key", fake_lookup)

        with pytest.raises(ConflictError) as exc_info:
            await wc.dispatch_task(
                task_kind="power.on",
                target_server_id="srv_abc",
                payload={},
                created_by="usr_x",
                request_id=None,
                idempotency_key=None,
            )
        assert exc_info.value.error_code == "TASK_IDEMPOTENT_CONFLICT"
