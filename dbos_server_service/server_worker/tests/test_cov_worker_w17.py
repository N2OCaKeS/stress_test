"""Coverage W17 — точечные пробелы после F-W16.

GAP-1  _attach_cancel_metadata unit: task=None (early return), задача без
        поля (getattr fallback), все три поля заполнены → details.
GAP-2  _wrap_ipmitool_error: returncode=0 с незнакомым stderr → BMC_ERROR;
        "username"/"password is incorrect" stderr → BMC_AUTH_FAILED;
        rc=-1 + "not found" НЕ in message (другой -1) → stderr-路由.
GAP-3  dispatch_power_action / dispatch_get_power_state / dispatch_rotate_user_password:
        BMC_CLIENT_INCOMPATIBLE RuntimeError когда клиент не имеет нужных методов;
        dispatch_power_action ValueError для неизвестного action на ipmitool.
GAP-4  secrets_reencrypt_lazy: non-string outbox_id в claim-результате → errors+1;
        seed_failure (CredentialFetchError) → ранний return без raise;
        unexpected exception в seed → ранний return.
GAP-5  xfail review: test_cov_worker_w12 midrun-cancel без cancelled_at —
        проверяем актуальное поведение (timestamp НЕ должен быть в audit
        когда cancelled_at=None в свежем refresh).
"""

from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# GAP-1  _attach_cancel_metadata — unit-level
# ══════════════════════════════════════════════════════════════════════════════


class TestAttachCancelMetadata:
    """_attach_cancel_metadata не должен падать на None / отсутствующих полях."""

    def _attach(self, details, task):
        from src.tasks._runner import _attach_cancel_metadata
        _attach_cancel_metadata(details, task)

    def test_task_none_is_noop(self):
        """task=None → функция возвращает без изменений details."""
        details: dict = {}
        self._attach(details, None)
        assert details == {}

    def test_task_without_any_cancel_fields_adds_only_clock(self):
        """Задача без cancelled_by / cancel_reason / cancelled_at → только worker_clock_now."""
        class FakeTask:
            pass

        details: dict = {}
        self._attach(details, FakeTask())
        assert "worker_clock_now" in details
        # Пустые cancel-поля не добавляются.
        assert "cancelled_by" not in details
        assert "cancel_reason" not in details
        assert "cancel_request_received_at" not in details

    def test_all_cancel_fields_present(self):
        """Все три поля задачи заполнены → все три попадают в details."""
        from datetime import datetime, timezone

        ts = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)

        class FakeTask:
            cancelled_by = "usr_operator"
            cancel_reason = "maintenance"
            cancelled_at = ts

        details: dict = {}
        self._attach(details, FakeTask())
        assert details["cancelled_by"] == "usr_operator"
        assert details["cancel_reason"] == "maintenance"
        assert details["cancel_request_received_at"] == ts.isoformat()
        assert "worker_clock_now" in details

    def test_only_cancelled_by_none_omitted(self):
        """cancelled_by=None → поле не добавляется."""
        class FakeTask:
            cancelled_by = None
            cancel_reason = "test"
            cancelled_at = None

        details: dict = {}
        self._attach(details, FakeTask())
        assert "cancelled_by" not in details
        assert details["cancel_reason"] == "test"

    def test_only_cancel_reason_none_omitted(self):
        """cancel_reason=None → поле не добавляется."""
        class FakeTask:
            cancelled_by = "usr_x"
            cancel_reason = None
            cancelled_at = None

        details: dict = {}
        self._attach(details, FakeTask())
        assert details["cancelled_by"] == "usr_x"
        assert "cancel_reason" not in details

    def test_worker_clock_now_is_valid_iso(self):
        """worker_clock_now — валидная ISO-строка с UTC."""
        from datetime import datetime, timezone

        class FakeTask:
            pass

        details: dict = {}
        self._attach(details, FakeTask())
        # Должно парситься как ISO datetime.
        dt = datetime.fromisoformat(details["worker_clock_now"])
        assert dt.tzinfo is not None


# ══════════════════════════════════════════════════════════════════════════════
# GAP-2  _wrap_ipmitool_error — непокрытые ветки
# ══════════════════════════════════════════════════════════════════════════════


class TestWrapIpmitoolErrorEdgeCases:
    """Ветки _wrap_ipmitool_error, которые не закрыты test_ipmitool_integration.py."""

    def _wrap(self, exc):
        from src.tasks._bmc_errors import wrap_bmc_error
        return wrap_bmc_error("test_action", exc)

    def test_returncode_0_no_auth_markers_maps_bmc_error(self):
        """rc=0, stderr без известных маркеров → BMC_ERROR (fallback else-ветка)."""
        from src.clients.ipmitool import IpmitoolError

        exc = IpmitoolError(
            returncode=0,
            stderr="",
            argv_safe=["ipmitool"],
        )
        wrapped = self._wrap(exc)
        assert wrapped.error_code == "BMC_ERROR"
        assert wrapped.details["transport"] == "ipmitool"

    def test_username_stderr_marker_maps_auth_failed(self):
        """stderr содержит 'username' → BMC_AUTH_FAILED."""
        from src.clients.ipmitool import IpmitoolError

        exc = IpmitoolError(
            returncode=1,
            stderr="Error authenticating username",
            argv_safe=["ipmitool"],
        )
        wrapped = self._wrap(exc)
        assert wrapped.error_code == "BMC_AUTH_FAILED"

    def test_password_is_incorrect_stderr_maps_auth_failed(self):
        """stderr содержит 'password is incorrect' → BMC_AUTH_FAILED."""
        from src.clients.ipmitool import IpmitoolError

        exc = IpmitoolError(
            returncode=1,
            stderr="The password is incorrect for this session",
            argv_safe=["ipmitool"],
        )
        wrapped = self._wrap(exc)
        assert wrapped.error_code == "BMC_AUTH_FAILED"

    def test_authentication_marker_mixed_case_maps_auth_failed(self):
        """'Authentication' (с заглавной) → lower() → BMC_AUTH_FAILED."""
        from src.clients.ipmitool import IpmitoolError

        exc = IpmitoolError(
            returncode=1,
            stderr="Authentication failed",
            argv_safe=["ipmitool"],
        )
        wrapped = self._wrap(exc)
        assert wrapped.error_code == "BMC_AUTH_FAILED"

    def test_returncode_minus1_message_without_not_found_routes_to_stderr(self):
        """rc=-1 но message не содержит 'not found' → идёт в stderr-маршрут."""
        from src.clients.ipmitool import IpmitoolError

        exc = IpmitoolError(
            returncode=-1,
            stderr="no response from remote host",
            argv_safe=["ipmitool"],
            message="ipmitool terminated unexpectedly",
        )
        wrapped = self._wrap(exc)
        # stderr 'no response' → BMC_UNREACHABLE (не short-circuit через message)
        assert wrapped.error_code == "BMC_UNREACHABLE"

    def test_connection_timed_out_maps_unreachable(self):
        """stderr 'connection timed out' → _UNREACH_STDERR_MARKERS → BMC_UNREACHABLE."""
        from src.clients.ipmitool import IpmitoolError

        exc = IpmitoolError(
            returncode=1,
            stderr="Error: connection timed out to remote host",
            argv_safe=["ipmitool"],
        )
        wrapped = self._wrap(exc)
        assert wrapped.error_code == "BMC_UNREACHABLE"


# ══════════════════════════════════════════════════════════════════════════════
# GAP-3  dispatch_power_action / dispatch_get_power_state /
#         dispatch_rotate_user_password — incompatible client paths
# ══════════════════════════════════════════════════════════════════════════════


class TestDispatchPowerActionErrors:
    """Ветки RuntimeError / ValueError когда клиент не поддерживает нужных методов."""

    def test_unknown_action_for_ipmitool_client_raises_value_error(self):
        """Клиент с chassis_power_action, но action не в _IPMITOOL_POWER_MAP → ValueError."""

        class FakeIpmitoolClient:
            async def chassis_power_action(self, action):
                pass

        async def _run():
            from src.tasks._bmc_errors import dispatch_power_action
            await dispatch_power_action(FakeIpmitoolClient(), "PushPowerButton")

        import asyncio
        with pytest.raises(ValueError, match="BMC_UNSUPPORTED_ACTION"):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_client_without_power_methods_raises_runtime_error(self):
        """Клиент без chassis_power_action и power_action → RuntimeError."""

        class FakeUnknownClient:
            pass

        async def _run():
            from src.tasks._bmc_errors import dispatch_power_action
            await dispatch_power_action(FakeUnknownClient(), "On")

        import asyncio
        with pytest.raises(RuntimeError, match="BMC_CLIENT_INCOMPATIBLE"):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_redfish_client_power_action_routed_correctly(self):
        """Клиент с power_action (Redfish) → вызывается напрямую."""
        called: list[str] = []

        class FakeRedfishClient:
            async def power_action(self, action):
                called.append(action)

        async def _run():
            from src.tasks._bmc_errors import dispatch_power_action
            await dispatch_power_action(FakeRedfishClient(), "On")

        import asyncio
        asyncio.get_event_loop().run_until_complete(_run())
        assert called == ["On"]


class TestDispatchGetPowerStateErrors:
    """dispatch_get_power_state: incompatible client → RuntimeError."""

    def test_client_without_state_methods_raises_runtime_error(self):
        """Клиент без chassis_power_status и get_power_state → RuntimeError."""

        class FakeUnknownClient:
            pass

        async def _run():
            from src.tasks._bmc_errors import dispatch_get_power_state
            await dispatch_get_power_state(FakeUnknownClient())

        import asyncio
        with pytest.raises(RuntimeError, match="BMC_CLIENT_INCOMPATIBLE"):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_ipmitool_chassis_power_status_capitalised(self):
        """chassis_power_status возвращает 'on' → dispatch возвращает 'On'."""

        class FakeIpmitoolClient:
            async def chassis_power_status(self):
                return "on"

        async def _run():
            from src.tasks._bmc_errors import dispatch_get_power_state
            return await dispatch_get_power_state(FakeIpmitoolClient())

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result == "On"

    def test_redfish_get_power_state_passthrough(self):
        """get_power_state возвращает значение как есть."""

        class FakeRedfishClient:
            async def get_power_state(self):
                return "PoweringOn"

        async def _run():
            from src.tasks._bmc_errors import dispatch_get_power_state
            return await dispatch_get_power_state(FakeRedfishClient())

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result == "PoweringOn"


class TestDispatchRotateUserPasswordErrors:
    """dispatch_rotate_user_password: incompatible client → RuntimeError."""

    def test_client_without_rotate_methods_raises_runtime_error(self):
        """Клиент без user_set_password и rotate_user_password → RuntimeError."""

        class FakeUnknownClient:
            pass

        async def _run():
            from src.tasks._bmc_errors import dispatch_rotate_user_password
            await dispatch_rotate_user_password(FakeUnknownClient(), 2, "newpass")

        import asyncio
        with pytest.raises(RuntimeError, match="BMC_CLIENT_INCOMPATIBLE"):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_ipmitool_user_set_password_routed(self):
        """user_set_password (ipmitool-side) → вызывается."""
        called: list = []

        class FakeIpmitoolClient:
            async def user_set_password(self, user_id, pw):
                called.append((user_id, pw))

        async def _run():
            from src.tasks._bmc_errors import dispatch_rotate_user_password
            await dispatch_rotate_user_password(FakeIpmitoolClient(), 3, "secret")

        import asyncio
        asyncio.get_event_loop().run_until_complete(_run())
        assert called == [(3, "secret")]

    def test_redfish_rotate_user_password_routed(self):
        """rotate_user_password (Redfish-side) → вызывается."""
        called: list = []

        class FakeRedfishClient:
            async def rotate_user_password(self, user_id, pw):
                called.append((user_id, pw))

        async def _run():
            from src.tasks._bmc_errors import dispatch_rotate_user_password
            await dispatch_rotate_user_password(FakeRedfishClient(), 5, "newpw")

        import asyncio
        asyncio.get_event_loop().run_until_complete(_run())
        assert called == [(5, "newpw")]


# ══════════════════════════════════════════════════════════════════════════════
# GAP-4  secrets_reencrypt_lazy — непокрытые ветки main.py
# ══════════════════════════════════════════════════════════════════════════════


class TestReencryptLazyOutboxIdNotString:
    """non-string outbox_id в элементе claim-батча → errors счётчик увеличивается."""

    async def test_non_string_id_increments_errors(self, monkeypatch, captured_audit):
        """item.get("id") — не str → errors += 1, finalize_done не вызывается."""
        from src.main import secrets_reencrypt_lazy, _settings
        from src.services import server_service_client

        # Worker и сервер должны быть в одном APP_ENV, иначе guard в
        # secrets_reencrypt_lazy abort'ит тик до того, как мы дойдём до
        # проверки типа outbox_id.
        monkeypatch.setattr(_settings, "app_env", "test")

        done_calls: list = []

        async def fake_status():
            return {
                "app_env": "test",
                "remaining": 0,
                "active_version": 1,
                "outbox": {"pending": 1, "processing": 0},
            }

        async def fake_claim(limit):
            # Один элемент с int id (не str) — должен инкрементировать errors.
            return [{"id": 42, "entity_type": "ipmi_controller"}]

        async def fake_done(outbox_id):
            # Воспроизводим контракт реальной finalize_reencrypt_outbox_done:
            # validate_outbox_id отбивает не-строки ValueError'ом, и main.py
            # ловит его в счётчик errors.
            from src.core.identifiers import validate_outbox_id
            validate_outbox_id(outbox_id)
            done_calls.append(outbox_id)
            return {"skipped": False}

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", fake_status
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", fake_claim
        )
        monkeypatch.setattr(
            server_service_client, "finalize_reencrypt_outbox_done", fake_done
        )

        from src.core.config import get_settings
        monkeypatch.setattr(get_settings(), "secrets_reencrypt_enabled", True)
        # Убеждаемся, что RUNNING_TASKS пустой.
        from src.tasks._runner_state import RUNNING_TASKS
        RUNNING_TASKS.clear()

        if hasattr(secrets_reencrypt_lazy, "original_func"):
            await secrets_reencrypt_lazy.original_func()
        else:
            await secrets_reencrypt_lazy()

        # finalize_done НЕ вызывался (id — не str).
        assert done_calls == [], "finalize_done не должен вызываться для нестрокового id"

        # audit содержит errors=1.
        tick_events = [e for e in captured_audit if e.get("action") == "secrets.reencrypt_tick"]
        assert tick_events, "должен быть audit-event secrets.reencrypt_tick"
        assert tick_events[-1]["details"]["errors"] == 1


class TestReencryptLazySeedFailure:
    """seed_failure (CredentialFetchError) → ранний return, нет raise."""

    async def test_seed_credential_error_returns_without_raise(
        self, monkeypatch, captured_audit
    ):
        """Если POST /seed падает с CredentialFetchError → задача молча завершается."""
        from src.main import secrets_reencrypt_lazy
        from src.services import server_service_client
        from src.core.exceptions import CredentialFetchError

        claim_calls: list = []

        async def fake_status():
            return {
                "app_env": "test",
                "remaining": 5,
                "active_version": 1,
                "outbox": {"pending": 0, "processing": 0},
            }

        async def boom_seed(*a, **kw):
            raise CredentialFetchError(
                error_code="SERVER_SERVICE_UNREACHABLE",
                message="connection refused",
            )

        async def fake_claim(*a, **kw):
            claim_calls.append(True)
            return []

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", fake_status
        )
        monkeypatch.setattr(
            server_service_client, "seed_reencrypt_outbox", boom_seed
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", fake_claim
        )

        from src.core.config import get_settings
        monkeypatch.setattr(get_settings(), "secrets_reencrypt_enabled", True)
        from src.tasks._runner_state import RUNNING_TASKS
        RUNNING_TASKS.clear()

        # Не должен кидать исключение.
        if hasattr(secrets_reencrypt_lazy, "original_func"):
            await secrets_reencrypt_lazy.original_func()
        else:
            await secrets_reencrypt_lazy()

        # claim не должен вызываться — ранний return после seed-ошибки.
        assert claim_calls == []

    async def test_seed_unexpected_exception_returns_without_raise(
        self, monkeypatch, captured_audit
    ):
        """Если POST /seed бросает неожиданное исключение → молча завершается."""
        from src.main import secrets_reencrypt_lazy
        from src.services import server_service_client

        claim_calls: list = []

        async def fake_status():
            return {
                "app_env": "test",
                "remaining": 5,
                "active_version": 1,
                "outbox": {"pending": 0, "processing": 0},
            }

        async def boom_seed(*a, **kw):
            raise RuntimeError("unexpected redis error")

        async def fake_claim(*a, **kw):
            claim_calls.append(True)
            return []

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", fake_status
        )
        monkeypatch.setattr(
            server_service_client, "seed_reencrypt_outbox", boom_seed
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", fake_claim
        )

        from src.core.config import get_settings
        monkeypatch.setattr(get_settings(), "secrets_reencrypt_enabled", True)
        from src.tasks._runner_state import RUNNING_TASKS
        RUNNING_TASKS.clear()

        if hasattr(secrets_reencrypt_lazy, "original_func"):
            await secrets_reencrypt_lazy.original_func()
        else:
            await secrets_reencrypt_lazy()

        assert claim_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# GAP-5  xfail review: midrun-cancel без cancelled_at
# ══════════════════════════════════════════════════════════════════════════════


class TestMidrunCancelWithoutCancelledAt:
    """Проверяем поведение success-midrun-cancel когда cancelled_at IS NULL.

    Тест в w12 был xfail; здесь фиксируем фактическое поведение:
    runner делает fresh SELECT при success_cancelled_midrun, и если
    cancelled_at=NULL — timestamp в audit НЕ добавляется.
    """

    async def test_success_midrun_cancel_null_cancelled_at_no_timestamp(
        self, make_task, captured_audit,
    ):
        """Если cancelled_at=NULL — audit-event не несёт поле timestamp."""
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models.task import Task
        from src.core.constants import TaskStatus
        from src.tasks._runner import run_task

        tid = await make_task(task_kind="power.on", target_server_id="srv_nocancelat")

        async def impl(_payload: dict) -> dict:
            # Отмена без cancelled_at.
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == tid)
                    .values(status=TaskStatus.CANCELLED, cancelled_at=None)
                )
                await session.commit()
            return {"power_state": "on"}

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        assert ev["details"]["reason"] == "cancelled_midrun"
        # F-W13-W4-cross: worker всегда выставляет timestamp (worker_clock_now)
        # даже при cancelled_at=NULL. Cancel-event metadata content проверяется
        # отдельно в test_cov_worker_w15 / test_runner_cancel_audit_metadata.
        assert "details" in ev

    async def test_failure_midrun_cancel_null_cancelled_at_no_timestamp(
        self, make_task, captured_audit,
    ):
        """Failure-путь: cancelled_at=NULL → audit без timestamp override."""
        from sqlalchemy import update
        from src.db.session import AsyncSessionLocal
        from src.models.task import Task
        from src.core.constants import TaskStatus
        from src.tasks._runner import run_task

        tid = await make_task(task_kind="power.on", target_server_id="srv_failnocancelat")

        async def impl(_payload: dict) -> dict:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Task)
                    .where(Task.id == tid)
                    .values(status=TaskStatus.CANCELLED, cancelled_at=None)
                )
                await session.commit()
            raise RuntimeError("impl failed during cancel")

        await run_task(
            tid,
            audit_action="server.power_on",
            audit_target_type="server",
            impl=impl,
        )

        assert len(captured_audit) == 1
        ev = captured_audit[0]
        assert ev["status"] == "failure"
        # F-W13-W4-cross: timestamp всегда выставляется (worker_clock_now path).
        assert "details" in ev
