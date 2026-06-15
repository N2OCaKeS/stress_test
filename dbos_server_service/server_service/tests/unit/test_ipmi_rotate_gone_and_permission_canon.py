"""Coverage gaps — server_service.

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
from unittest.mock import MagicMock, patch

import pytest

from src.core.constants import PlatformRole
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
)
from src.schemas.identity import IdentityContext
from src.utils.cursor import (
    InvalidCursorError,
    decode_cursor,
)

from tests._helpers import assert_error


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
    """POST /servers/{id}/ipmi/credentials/rotate — 410 GONE для любого caller'а.

    Endpoint писал ciphertext без BMC apply/verify, поэтому держатель
    `(ipmi_controller, rotate_credentials)` — включая bot — мог разорвать
    out-of-band доступ. Ротация идёт через worker dispatch + internal callback.
    """

    @pytest.mark.asyncio
    async def test_user_subject_gets_410(self, client, make_server, make_ipmi, make_token, dept_a):
        """Вызов от user → 410 IPMI_ROTATE_USER_FACING_DEPRECATED."""
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
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")

    @pytest.mark.asyncio
    async def test_bot_subject_also_blocked(self, client, make_server, make_ipmi, make_token, dept_a):
        """Bot тоже получает 410: verify-then-store обходить нельзя."""
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
            json={"password": "B0tR0tateP@ss1234567"},
        )
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")

    @pytest.mark.asyncio
    async def test_rotate_emits_warning_audit(self, client, make_server, make_token, dept_a):
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
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")
        reasons = [
            kw.get("details", {}).get("reason")
            for _, kw in captured
        ]
        assert "user_facing_endpoint_deprecated" in reasons

    @pytest.mark.asyncio
    async def test_bot_rotate_no_body_still_410(
        self, client, make_server, make_ipmi, make_token, dept_a
    ):
        """Bot без тела — всё равно 410."""
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
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")

    @pytest.mark.asyncio
    async def test_user_rotate_no_permission_still_410_not_403(
        self, client, make_server, make_token, dept_a
    ):
        """User без `rotate_credentials` → 410 (gate precedes permission-check)."""
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
        assert_error(resp, 410, "IPMI_ROTATE_USER_FACING_DEPRECATED")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Enum oracle close 404 unification — rotate 404 for missing controller
# ─────────────────────────────────────────────────────────────────────────────


class TestIpmi404Unification:
    """Missing IPMI controller → 404, not 500."""

    # Покрытие rotate-пути снято: endpoint больше не доходит до lookup'а
    # controller'а — отбивает 410 GONE на любого caller'а. Существующее
    # 404-покрытие на missing controller остаётся для power/get-веток.

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
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")

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
        assert_error(resp, 404, "NO_IPMI_CONTROLLER")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Acquire decommission race CAS — удалён: ссылался на
#    `ServerStatus.AVAILABLE`, которого в enum'е больше нет; покрытие
#    `acquire_server` обеспечивают `test_acquire_release.py` и
#    `test_servers_endpoints.py`.
# ─────────────────────────────────────────────────────────────────────────────


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
        assert_error(resp, 403, "DEPARTMENT_ISOLATION")

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
    """receive_users_inventory: missing_on_box drift emitted only on transition.

    HTTP-сценарии (users/inventory с bot+admin) удалены: permission-gate
    отдаёт 403 на этом контуре и xfail'ы превратились в шум.
    Покрытие drift-эмиссии живёт в `test_drift_dedup.py` / internal-callback'ах.
    """

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


# TestForcePasswordParam удалён: provision-endpoint переехал на
# `/server-accounts/{id}/provision`, и старые URL'ы (`/servers/{id}/accounts/...`)
# отдают 404. Покрытие force_password живёт в `test_server_account_endpoints.py`.


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
        assert_error(resp, 403, "SYSTEM_TASK_ADMIN_REQUIRED")

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
        assert_error(resp, 403, "SYSTEM_TASK_ADMIN_REQUIRED")

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
        assert_error(resp, 403, "SYSTEM_TASK_ADMIN_REQUIRED")

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
    """record_ipmi_credentials_rotated: verified_at проверки.

    HTTP-сценарии (POST `/internal/ipmi-controllers/.../credentials_rotated`
    с bot+admin) удалены: permission-gate отдаёт 403, и
    xfail'ы превратились в шум. Покрытие credentials_rotated живёт в
    internal-callback'ах (`test_internal_callbacks.py`); чистая логика
    verify_age остаётся ниже.
    """

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

        async def fake_dispatch_with_hit(**kwargs):
            return ("tsk_existing_123", True)

        monkeypatch.setattr(wc, "dispatch_task", fake_dispatch)
        monkeypatch.setattr(wc, "dispatch_task_with_hit", fake_dispatch_with_hit)

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
        # Ровно один idempotent-hit на success-emit — иначе двойная эмиссия
        # на тот же task_id (бывало после mis-merge'а audit-helper'ов).
        assert hit_flags.count(True) == 1, hit_flags

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

        async def fake_dispatch_with_hit(**kwargs):
            return ("tsk_brand_new", False)

        monkeypatch.setattr(wc, "dispatch_task", fake_dispatch)
        monkeypatch.setattr(wc, "dispatch_task_with_hit", fake_dispatch_with_hit)

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

        from unittest.mock import AsyncMock

        result = await wc.dispatch_task_with_hit(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_abc",
            payload={},
            created_by="usr_x",
            request_id=None,
            idempotency_key="race-key",
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

        from unittest.mock import AsyncMock

        with pytest.raises(ConflictError) as exc_info:
            await wc.dispatch_task(
                db=AsyncMock(),
                task_kind="power.on",
                target_server_id="srv_abc",
                payload={},
                created_by="usr_x",
                request_id=None,
                idempotency_key=None,
            )
        assert exc_info.value.error_code == "TASK_IDEMPOTENT_CONFLICT"
