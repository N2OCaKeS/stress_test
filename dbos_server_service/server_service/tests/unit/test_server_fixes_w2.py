"""Юнит-тесты для пятёрки фиксов в server_service.

Покрывают:
  * permission ДО visibility в `users_inventory` (нет enumeration-oracle на 403);
  * `missing_on_box` drift эмитится только при переходе `True → False` и
    несёт `is_new_drift=True` в details;
  * `rotate_credentials` (legacy) docstring/summary помечен как fallback,
    не обещает verify-механизма на этом endpoint'е;
  * `account.provision` для discovered-аккаунта без пароля без
    `?force_password=true` отбивается 422 `DISCOVERED_NO_PASSWORD_NEEDS_EXPLICIT_FORCE`;
  * системные task'и опознаются через whitelist `_SYSTEM_TASK_KINDS`, не
    через неявный (target_server_id IS NULL AND created_by IS NULL).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.api.v1.endpoints import tasks as tasks_endpoint
from src.api.v1.endpoints import inventory as inventory_endpoint
from src.core.constants import AccountSource, PlatformRole
from src.core.exceptions import AuthorizationError
from src.models import ServerAccountServer

BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Fix 1: permission → visibility в users_inventory ────────────────────────


class TestUsersInventoryPermissionBeforeVisibility:
    async def test_reader_on_nonexistent_returns_403_not_404(
        self, client, reader_token_a, monkeypatch,
    ):
        """Reader без INVENTORY_TRIGGER попадает в 403 даже на несуществующем
        сервере — visibility check не происходит, поэтому он не может через
        отличие 403/404 узнать, существует ли `srv_phantom` в чужом dept'е.
        """
        calls: list[str] = []

        async def spy_get_server(db, identity, server_id):
            calls.append(server_id)
            from src.core.exceptions import NotFoundError
            raise NotFoundError(
                error_code="SERVER_NOT_FOUND", message="Server not found",
            )

        monkeypatch.setattr(
            inventory_endpoint.server_svc, "get_server", spy_get_server,
        )

        resp = await client.post(
            f"{BASE}/servers/srv_phantom/users/inventory",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403, resp.text
        # Permission check сработал до visibility: get_server вообще не звали.
        assert calls == []


# ── Fix 2: missing_on_box drift — только на True → False переходе ───────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestMissingOnBoxDriftIsNew:
    async def test_first_missing_emits_drift_with_is_new_true(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        monkeypatch,
    ):
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr("src.services.audit_service.emit", fake_emit)
        monkeypatch.setattr(
            "src.services.internal_service.audit_service.emit", fake_emit,
        )

        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="ghost")
        # Связка свежая — present_on_server=True по дефолту фабрики.
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        assert link.present_on_server is True

        resp = await client.post(
            f"{BASE}/internal/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json={"users": []},
        )
        assert resp.status_code == 200
        assert resp.json()["drifted"] == 1

        drifts = [e for e in captured if e["action"] == "server_account.drift_detected"]
        assert len(drifts) == 1
        assert drifts[0]["details"]["drift"] == "missing_on_box"
        assert drifts[0]["details"]["is_new_drift"] is True

    async def test_repeated_missing_does_not_emit(
        self, client, worker_bot_token_a, make_server, make_account, db, dept_a,
        monkeypatch,
    ):
        srv = await make_server(department_id=dept_a)
        acc = await make_account(server_id=srv.id, login="ghost")
        # Первый прогон с пустым списком пометит связку как отсутствующую.
        first = await client.post(
            f"{BASE}/internal/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json={"users": []},
        )
        assert first.status_code == 200

        await db.commit()
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv.id,
            )
        )).scalar_one()
        assert link.present_on_server is False

        # Второй прогон, пользователя по-прежнему нет.
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        monkeypatch.setattr(
            "src.services.internal_service.audit_service.emit", fake_emit,
        )
        second = await client.post(
            f"{BASE}/internal/servers/{srv.id}/users/inventory",
            headers=_hdr(worker_bot_token_a), json={"users": []},
        )
        assert second.status_code == 200
        # drifted-счётчик НЕ растёт повторно, drift-emit не отправляется.
        assert second.json()["drifted"] == 0
        drifts = [e for e in captured if e["action"] == "server_account.drift_detected"]
        assert drifts == []


# ── Fix 3: rotate_credentials docstring/summary ─────────────────────────────


class TestRotateCredentialsDocstring:
    def test_summary_marks_endpoint_as_legacy_fallback(self):
        """Маркеры контракта: summary указывает на 410 GONE для user,
        description явно говорит про bot-only fallback и про то, что сам
        endpoint verify-proof НЕ проверяет (проверка в internal callback).
        """
        from src.api.v1.endpoints.ipmi import router

        # Найти маршрут /credentials/rotate.
        rotate_route = None
        for r in router.routes:
            if getattr(r, "path", "").endswith("/credentials/rotate"):
                rotate_route = r
                break
        assert rotate_route is not None, "rotate route missing"

        summary = rotate_route.summary or ""
        description = rotate_route.description or ""
        assert "legacy" in summary.lower() or "410" in summary
        # Контракт после P0: bot-only fallback + указатель на internal callback.
        assert "bot" in description.lower()
        assert "internal" in description.lower()
        # Прямое признание: сам endpoint не проверяет verify.
        assert "verify-proof НЕ проверяет" in description or "не проверяет" in description.lower()


# ── Fix 4: discovered без пароля требует force_password=true ────────────────


class TestProvisionForcePasswordGuard:
    async def test_discovered_without_password_default_returns_422(
        self, client, operator_token_a, make_server, make_account, db,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ghost", password=None)
        # Превратить аккаунт в discovered (фабрика дефолтит на managed).
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/provision?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error_code"] == "DISCOVERED_NO_PASSWORD_NEEDS_EXPLICIT_FORCE"

    async def test_discovered_without_password_force_true_proceeds(
        self, client, operator_token_a, make_server, make_account, db, monkeypatch,
    ):
        captured: list[dict] = []

        async def fake_dispatch(*, task_kind, target_server_id, payload,
                                created_by, request_id,
                                target_resource_id=None, idempotency_key=None):
            captured.append({"task_kind": task_kind, "payload": payload})
            return f"tsk_{task_kind.replace('.', '_')}_1"

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            fake_dispatch,
        )

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ghost", password=None)
        acc.source = AccountSource.DISCOVERED.value
        await db.flush()
        await db.commit()

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/provision"
            f"?server_id={srv.id}&force_password=true",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202, resp.text
        assert len(captured) == 1
        # Сгенерированный пароль и ключи поехали воркеру, force_replace=True.
        payload = captured[0]["payload"]
        assert "password_plaintext" in payload
        assert "ssh_public_key" in payload
        assert payload["force_replace"] is True


# ── Fix 5: системные task'и опознаются по whitelist task_kind ───────────────


def _identity(
    *,
    user_id: str = "usr_test",
    department_id: str | None = "dep_a",
    platform_role: PlatformRole | None = None,
    service_roles: dict[str, list[str]] | None = None,
):
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
def patch_permissions(monkeypatch):
    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "src.api.v1.endpoints.tasks.permissions.require_action", _ok,
    )


class TestSystemTaskWhitelist:
    async def test_whitelist_constant_holds_only_known_kinds(self):
        from src.api.v1.endpoints.tasks import _SYSTEM_TASK_KINDS

        assert _SYSTEM_TASK_KINDS == frozenset({
            "system.heartbeat",
            "worker.heartbeat",
            "tasks.sweep_orphaned",
            "tasks.recover_scheduled_retries",
            "tasks.cleanup_completed_old",
            "worker.cleanup_stale_heartbeats",
            "audit_outbox.cleanup_published_old",
            "secrets.reencrypt_lazy",
        })

    async def test_user_task_with_both_nulls_no_longer_blocked(
        self, db, patch_permissions, monkeypatch,
    ):
        """Пользовательская task с `target_server_id=None` и `created_by=None`
        (например, синтетический ручной dispatch без актора через PAT) теперь
        идёт по обычному пути, не упирается в system_task_admin_required.

        Сценарий невозможен через текущий roster endpoint'ов (worker_client
        проставляет created_by), но whitelist делает контракт независимым от
        будущего расширения.
        """
        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "inventory.sync",  # НЕ системный kind
                "created_by": None,
            }

        async def fake_cancel(*, task_id_value, cancelled_by, cancel_reason):
            return {
                "found": True,
                "cancelled": True,
                "previous_status": "queued",
                "task_kind": "inventory.sync",
                "target_server_id": None,
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
            task_id="tsk_user_no_target",
            body=None,
            db=db,
        )
        assert resp.status == "cancelled"

    async def test_heartbeat_kind_still_requires_account_admin(
        self, db, patch_permissions, monkeypatch,
    ):
        """`worker.heartbeat` остаётся в whitelist — dept_admin его НЕ отменяет."""
        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "worker.heartbeat",
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
                task_id="tsk_heartbeat",
                body=None,
                db=db,
            )
        assert exc_info.value.error_code == "SYSTEM_TASK_ADMIN_REQUIRED"
        cancel_mock.assert_not_awaited()
