"""Гарда на отмену системных task'ов (heartbeat/sweep/cleanup_completed).

Системные task'и заводятся scheduler'ом в `server_worker` без `created_by` и
без `target_server_id`. До этого фикса любой dept-admin с (task, cancel)
мог их отменить и положить кластерный worker health. После — для системных
требуется платформенная роль `account_admin`.

Платформенная роль реально режется ещё до endpoint'а в `platform_admin_guard`
middleware (см. `src/middleware/platform_admin_guard.py`), но endpoint держит
свою проверку как defence-in-depth: если когда-нибудь middleware снимут или
поменяют, бизнес-инвариант сохраняется на уровне роутера.

Тесты гоняем напрямую через endpoint-функцию, без полного ASGI-стека, чтобы:

* проверить именно логику роутера (а не middleware);
* иметь возможность подсунуть identity с `platform_role=account_admin`
  и убедиться, что endpoint его пропускает (через ASGI middleware блокирует).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.api.v1.endpoints import tasks as tasks_endpoint
from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError
from src.schemas.task import TaskCancelRequest


def _identity(
    *,
    user_id: str = "usr_test",
    department_id: str | None = "dep_a",
    platform_role: PlatformRole | None = None,
    service_roles: dict[str, list[str]] | None = None,
):
    """Минимальный IdentityContext для прямого вызова endpoint-функции."""
    from src.schemas.identity import IdentityContext

    return IdentityContext(
        user_id=user_id,
        username="tester",
        department_id=department_id,
        department_name=None,
        allowed_services=["server_service"],
        service_roles=service_roles or {"server_service": ["admin"]},
        is_banned=False,
        platform_role=platform_role,
        subject_type="user",
    )


@pytest.fixture
def captured_audit(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    monkeypatch.setattr(
        "src.api.v1.endpoints.tasks.audit_service.emit", fake_emit,
    )
    monkeypatch.setattr("src.services.audit_service.emit", fake_emit)
    monkeypatch.setattr(
        "src.services.server.audit_service.emit", fake_emit,
    )
    return captured


@pytest.fixture
def patch_permissions(monkeypatch):
    """`permissions.require_action` всегда ОК — мы тестируем уровень после."""
    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "src.api.v1.endpoints.tasks.permissions.require_action", _ok,
    )


class TestSystemTaskGuard:
    """target_server_id=None И created_by=None → требует account_admin."""

    async def test_dept_admin_blocked_from_system_task(
        self, db, captured_audit, patch_permissions, monkeypatch,
    ):
        """Dept-admin без platform_role=account_admin → 403."""
        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "heartbeat",
                "created_by": None,
            }

        cancel_mock = AsyncMock()
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "_fetch_task_status_and_meta", fake_fetch,
        )
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "cancel_task", cancel_mock,
        )

        identity = _identity(
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
            service_roles={"server_service": ["admin"]},
        )
        with pytest.raises(AuthorizationError) as exc_info:
            await tasks_endpoint.cancel_task_endpoint(
                identity=identity,
                task_id="tsk_heartbeat_sys",
                body=None,
                db=db,
            )
        assert exc_info.value.error_code == "SYSTEM_TASK_ADMIN_REQUIRED"

        # cancel_task не вызывался — гарда сработала до mutate.
        cancel_mock.assert_not_awaited()

        # denied-audit с конкретной причиной.
        ev = [e for e in captured_audit if e["action"] == "task.cancelled"]
        assert len(ev) == 1
        assert ev[0]["status"] == "denied"
        assert ev[0]["details"]["reason"] == "system_task_admin_required"
        assert ev[0]["details"]["task_kind"] == "heartbeat"
        assert ev[0]["details"]["target_server_id"] is None

    async def test_account_admin_passes_system_task_guard(
        self, db, captured_audit, patch_permissions, monkeypatch,
    ):
        """`platform_role=account_admin` → guard пропускает, дальше идёт cancel."""
        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "sweep",
                "created_by": None,
            }

        async def fake_cancel(*, task_id_value, cancelled_by, cancel_reason):
            return {
                "found": True,
                "cancelled": True,
                "previous_status": "queued",
                "task_kind": "sweep",
                "target_server_id": None,
            }

        monkeypatch.setattr(
            tasks_endpoint.worker_client, "_fetch_task_status_and_meta", fake_fetch,
        )
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "cancel_task", fake_cancel,
        )

        identity = _identity(
            user_id="usr_root",
            department_id=None,
            platform_role=PlatformRole.ACCOUNT_ADMIN,
            service_roles={},
        )
        resp = await tasks_endpoint.cancel_task_endpoint(
            identity=identity,
            task_id="tsk_sweep_sys",
            body=TaskCancelRequest(reason="cluster cleanup"),
            db=db,
        )
        assert resp.task_id == "tsk_sweep_sys"
        assert resp.status == "cancelled"
        assert resp.previous_status == "queued"

        ev = [e for e in captured_audit if e["action"] == "task.cancelled"]
        # один success-emit, без denied
        assert len(ev) == 1
        assert ev[0]["status"] == "success"

    async def test_target_server_present_keeps_old_behavior(
        self, db, captured_audit, patch_permissions, monkeypatch, make_server,
    ):
        """target_server_id != None → гарда системных task'ов не применяется,
        работает прежний dept-isolation путь (dept_admin своего dep'а проходит).
        """
        srv = await make_server(department_id="dep_a")

        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": srv.id,
                "task_kind": "power.on",
                "created_by": None,
            }

        async def fake_cancel(*, task_id_value, cancelled_by, cancel_reason):
            return {
                "found": True,
                "cancelled": True,
                "previous_status": "queued",
                "task_kind": "power.on",
                "target_server_id": srv.id,
            }

        monkeypatch.setattr(
            tasks_endpoint.worker_client, "_fetch_task_status_and_meta", fake_fetch,
        )
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "cancel_task", fake_cancel,
        )

        identity = _identity(
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
            service_roles={"server_service": ["admin"]},
        )
        resp = await tasks_endpoint.cancel_task_endpoint(
            identity=identity,
            task_id="tsk_power_user",
            body=None,
            db=db,
        )
        assert resp.status == "cancelled"
        assert resp.previous_status == "queued"

        ev = [e for e in captured_audit if e["action"] == "task.cancelled"]
        assert len(ev) == 1
        assert ev[0]["status"] == "success"
