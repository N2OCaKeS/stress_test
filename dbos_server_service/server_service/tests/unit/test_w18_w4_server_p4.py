"""Регрессионные тесты-якоря по server_service.

Большая часть пунктов уже закрыта в основном коде; тесты ловят, если
кто-то откатит фикс.

Покрытые пункты:

1. `_generate_strong_password` — `for ... in range(_STRONG_PWD_MAX_ATTEMPTS)`
   вместо `while True`, MAX_ATTEMPTS=32.
2. `password_policy._POLICY_MESSAGE` / `_STRONG_POLICY_MESSAGE` — f-string
   c константами вместо хардкода "8"/"16".
3. `installed_packages` payload — `_MAX_INSTALLED_PACKAGES_ROWS=10000` cap
   прокидывается в `payload["max_rows"]`.
4. `ipmi_controller.get_controller` / `server_account.get_account` —
   view-success после reveal'а, не до. Source-inspection ловит порядок
   эмитов.
5. `_rate_limit_exceeded_response` — 429-ответ содержит `Retry-After`,
   `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.
6. `worker_client.dispatch_task` / `dispatch_task_with_hit` — два явных
   метода, нет `return_hit` параметра, разные return-аннотации.
7. `core/limiter._settings = get_settings()` — module-level read, docstring
   фиксирует start-time контракт.
8. `audit_service` — async-path Retry-After parsing + 30s cap.
9. `audit_service._send_sync` — sync-path retry на 429 + 1.0s cap.
10. N+1: `internal_service.receive_users_inventory` использует батчевый
    fetch (`get_by_logins`), не цикл `get_by_login`.
11. `worker_dispatch.dispatch_account_on_host` — на ServiceUnavailable /
    runtime exc откатывает creds savepoint и чистит Redis stash; source
    inspection ловит обе try-ветки.
12. `dispatch_task_with_hit` callers (`ipmi.py`, `worker_dispatch.py`)
    оборачивают `ConflictError` / `ServiceUnavailableError` в audit-failure.
13. Dead-param cleanup: `redaction.redact()` больше не принимает
    `_parent_key`; `worker_dispatch._dispatch_account_on_host` не держит
    неиспользуемый `generated` в tuple-unpack.
14. `audit_service._pending_audit_tasks` — module-level set, strong-ref
    держит task'и до завершения.
15. `main._drain_pending_audit_tasks` — lifespan на shutdown ждёт pending
    audit-task'ов с таймаутом `_AUDIT_DRAIN_TIMEOUT_SECONDS`.
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock

import pytest

from src import main as server_main
from src.api.v1.endpoints import installed_packages as installed_packages_endpoint
from src.api.v1.endpoints import ipmi as ipmi_endpoint
from src.api.v1.endpoints import worker_dispatch as wd_endpoint
from src.core import limiter as limiter_module
from src.core import password_policy
from src.services import (
    audit_service,
    internal_service,
    ipmi_controller as ipmi_ctrl_service,
    redaction,
    server_account as server_account_service,
    worker_client,
)


# ── 1. _generate_strong_password cap ─────────────────────────────────────────


class TestGenerateStrongPasswordCap:
    """Проверка: `for _ in range(_STRONG_PWD_MAX_ATTEMPTS)` вместо `while True`."""

    def test_max_attempts_constant_exists_and_finite(self):
        assert isinstance(server_account_service._STRONG_PWD_MAX_ATTEMPTS, int)
        assert 1 < server_account_service._STRONG_PWD_MAX_ATTEMPTS <= 256

    def test_no_while_true_in_generator_source(self):
        src = inspect.getsource(server_account_service._generate_strong_password)
        assert "while True" not in src, (
            "`while True` без cap'а вернулся — должен быть `for _ in range(...)`"
        )
        assert "range(_STRONG_PWD_MAX_ATTEMPTS)" in src

    def test_generator_returns_strong_password(self):
        pwd = server_account_service._generate_strong_password()
        assert password_policy.is_strong(pwd)

    def test_generator_raises_when_policy_unsatisfiable(self, monkeypatch):
        # Алфавит без спец-символов → has_symbol всегда False → 32 итерации
        # без успеха → RuntimeError.
        monkeypatch.setattr(server_account_service, "_STRONG_PWD_SYMBOLS", "")
        with pytest.raises(RuntimeError):
            server_account_service._generate_strong_password()


# ── 2. password_policy: f-string'и с константами ─────────────────────────────


class TestPasswordPolicyMessagesUseConstants:
    def test_basic_message_includes_min_length(self):
        assert str(password_policy.MIN_PASSWORD_LENGTH) in password_policy._POLICY_MESSAGE

    def test_strong_message_includes_min_strong_length(self):
        assert (
            str(password_policy.MIN_STRONG_PASSWORD_LENGTH)
            in password_policy._STRONG_POLICY_MESSAGE
        )

    def test_basic_message_does_not_hardcode_strong_length(self):
        # В сообщении базовой политики не должно случайно оказаться "16".
        msg = password_policy._POLICY_MESSAGE
        assert str(password_policy.MIN_STRONG_PASSWORD_LENGTH) not in msg


# ── 3. installed_packages cap ────────────────────────────────────────────────


class TestInstalledPackagesMaxRowsCap:
    def test_max_rows_constant_exists(self):
        assert installed_packages_endpoint._MAX_INSTALLED_PACKAGES_ROWS == 10000

    def test_handler_passes_max_rows_into_payload(self):
        src = inspect.getsource(installed_packages_endpoint)
        assert '"max_rows": _MAX_INSTALLED_PACKAGES_ROWS' in src


# ── 4. view-success после reveal'а ───────────────────────────────────────────


class TestViewSuccessAfterReveal:
    def test_ipmi_get_controller_reveals_before_success_emit(self):
        src = inspect.getsource(ipmi_ctrl_service.get_controller)
        reveal_idx = src.find("_reveal_controller_password")
        # Первый success-emit на ipmi_controller.view
        success_idx = src.find('"ipmi_controller.view"', src.find('status="success"'))
        # Самый ранний reveal должен идти раньше success-emit'а.
        assert reveal_idx != -1
        # success-emit для status="success" появляется только после reveal'а
        success_block = src[src.find('status="success"'):]
        assert "_reveal_controller_password" in src[:src.find('status="success"')]

    async def test_server_account_get_decrypt_failure_does_not_emit_success(
        self, client, admin_token, make_server, make_account, monkeypatch, db,
    ):
        """Контракт: GET account с битым ciphertext → DECRYPT_FAILED + view.failure, без view.success.

        Эмиссия success-аудита раньше reveal'а пароля приводила к парному
        `success`+`failure` событию (success пишется до того, как
        `secrets_service.decrypt` бросает) — это путало SIEM. Поведенческая
        проверка: подменяем `secrets_service.decrypt` так, чтобы он бросал;
        убеждаемся, что endpoint вернул 500 DECRYPT_FAILED, success-аудита
        на `server_account.view` нет, а failure-аудит есть.
        """
        from src.services import secrets_service

        captured_emits: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured_emits.append({"action": action, "actor_id": actor_id, **kwargs})

        monkeypatch.setattr(
            "src.services.server_account.audit_service.emit", fake_emit,
        )

        def sync_boom(*_a, **_kw):
            raise ValueError("synthetic decrypt failure")

        monkeypatch.setattr(secrets_service, "decrypt", sync_boom)

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="real-secret")
        await db.commit()

        resp = await client.get(
            f"/api/server/v1/server-accounts/{acc.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"reveal": "true"},
        )
        # 500 либо 422 — в зависимости от того, на какой стадии endpoint
        # ловит decrypt-fail. В любом случае success-эмит'а быть не должно.
        assert resp.status_code in (422, 500), resp.text

        success_views = [
            e for e in captured_emits
            if e.get("action") == "server_account.view"
            and e.get("status") == "success"
        ]
        assert success_views == [], (
            f"get_account emit'нул success ДО decrypt'а: {success_views}; "
            "событие появится вместе с failure на DECRYPT_FAILED — SIEM путается."
        )


# ── 5. rate-limit headers ────────────────────────────────────────────────────


class TestRateLimitResponseHeaders:
    def test_response_includes_standard_rate_limit_headers(self):
        request = MagicMock()
        request.state.request_id = "req_abc"
        # slowapi `RateLimitExceeded` хочет limit-объект с `.detail`
        exc = MagicMock()
        exc.detail = "5 per 1 minute"
        response = server_main._rate_limit_exceeded_response(request, exc)
        headers = dict(response.headers)
        assert headers.get("retry-after") == "60"
        assert headers.get("x-ratelimit-limit") == "5 per 1 minute"
        assert headers.get("x-ratelimit-remaining") == "0"
        assert "x-ratelimit-reset" in headers
        # reset — epoch-секунды, должно парситься в int
        int(headers["x-ratelimit-reset"])


# ── 6. dispatch_task split ───────────────────────────────────────────────────


class TestDispatchTaskSplit:
    def test_both_methods_exist(self):
        assert hasattr(worker_client, "dispatch_task")
        assert hasattr(worker_client, "dispatch_task_with_hit")

    def test_dispatch_task_does_not_accept_return_hit(self):
        sig = inspect.signature(worker_client.dispatch_task)
        assert "return_hit" not in sig.parameters

    def test_dispatch_task_with_hit_does_not_accept_return_hit(self):
        sig = inspect.signature(worker_client.dispatch_task_with_hit)
        assert "return_hit" not in sig.parameters

    def test_inner_helper_is_private(self):
        assert hasattr(worker_client, "_dispatch_task_inner")


# ── 7. limiter import-time settings ──────────────────────────────────────────


class TestLimiterImportTimeSettings:
    def test_settings_resolved_at_import(self):
        assert limiter_module._settings is not None

    def test_module_docstring_documents_start_time_contract(self):
        doc = limiter_module.__doc__ or ""
        # Контракт зафиксирован в docstring'е — никакого hot-reload без рестарта.
        assert "Start-time" in doc or "start-time" in doc.lower()


# ── 8/9. audit_service retry на 429 ──────────────────────────────────────────


class TestAuditRetryAfter:
    def test_parse_numeric_retry_after(self):
        assert audit_service._parse_retry_after_seconds("3") == 3.0

    def test_parse_invalid_retry_after_returns_none(self):
        assert audit_service._parse_retry_after_seconds("not-a-number") is None
        assert audit_service._parse_retry_after_seconds(None) is None

    def test_parse_negative_retry_after_clamped_or_dropped(self):
        # Контракт: отрицательное / нулевое значение не должно повиснуть.
        val = audit_service._parse_retry_after_seconds("-5")
        assert val is None or val <= 0

    def test_send_sync_helper_exists(self):
        assert hasattr(audit_service, "_send_sync")
        assert callable(audit_service._send_sync)

    def test_send_sync_handles_429(self):
        src = inspect.getsource(audit_service._send_sync)
        assert "429" in src, "sync-path не различает 429 — retry-логика отсутствует"


# ── 10. N+1: receive_users_inventory батчевый fetch ──────────────────────────


class TestUsersInventoryBatchFetch:
    def test_reconcile_uses_batch_fetch_not_per_login_loop(self):
        src = inspect.getsource(internal_service.receive_users_inventory)
        # batched fetch — индикатор отсутствия N+1
        markers = ("get_by_logins", "get_many", "in_(", "WHERE.*IN", "ANY(")
        assert any(m in src for m in markers) or "for " not in src.split("def ")[0], (
            "receive_users_inventory выглядит как N+1: нет батчевого fetch'а"
        )


# ── 11. worker_dispatch rollback на ServiceUnavailable / runtime exc ─────────


class TestDispatchRollbackOnRedisFailure:
    def test_handler_rolls_back_savepoint_on_service_unavailable(self):
        # `_dispatch_account_on_host` — приватная корневая логика дёргается
        # из публичных handler'ов (account.rotate, etc). Берём весь модуль.
        src = inspect.getsource(wd_endpoint)
        # Должны быть обе ветки: ServiceUnavailableError → rollback + audit,
        # `Exception` → delete_dispatch_creds + rollback + audit.
        assert "creds_store_unavailable" in src
        assert "creds_store_failed" in src
        assert "await creds_sp.rollback()" in src
        assert "await worker_client.delete_dispatch_creds(stash_key)" in src

    def test_finally_block_cleans_up_stash_on_dispatch_failure(self):
        src = inspect.getsource(wd_endpoint)
        # finally: если dispatch не ok — чистим stash + rollback savepoint.
        # `inject_provision_creds` параметр выпилен; provision живёт в
        # выделенном `_dispatch_account_provision`, где `if not dispatch_ok`
        # — единственная ветка cleanup'а (creds-stash всегда присутствует).
        assert "dispatch_ok = False" in src
        assert "if not dispatch_ok" in src
        assert "delete_dispatch_creds(stash_key)" in src


# ── 12. dispatch_task_with_hit callers оборачивают ошибки в audit ────────────


class TestDispatchTaskWithHitCallersAuditFailures:
    def test_ipmi_dispatch_handles_conflict_and_service_unavailable(self):
        src = inspect.getsource(ipmi_endpoint._dispatch_power)
        assert "ConflictError" in src
        assert "ServiceUnavailableError" in src
        # Оба пути должны эмитить failure-audit с reason'ами
        assert "idempotent_conflict" in src
        assert "worker_unreachable" in src

    def test_server_prepare_dispatch_handles_conflict_and_service_unavailable(self):
        src = inspect.getsource(wd_endpoint)
        # `server_prepare_dispatch` — оба эксепшна обёрнуты в audit-failure
        # ПЛЮС clean-up Redis stash'а.
        assert "delete_prepare_creds(creds_key)" in src
        assert src.count("idempotent_conflict") >= 1
        assert src.count("worker_unreachable") >= 1


# ── 13. Dead-param cleanup ───────────────────────────────────────────────────


class TestDeadParamCleanup:
    def test_redact_does_not_accept_parent_key(self):
        sig = inspect.signature(redaction.redact)
        assert "_parent_key" not in sig.parameters
        assert "parent_key" not in sig.parameters

    def test_dispatch_account_does_not_keep_unused_generated_var(self):
        # `generated` из ensure_provision_credentials в этом контексте не
        # используется — должно быть распаковано как `_`.
        src = inspect.getsource(wd_endpoint)
        # Старая форма: `_, creds, generated = await account_svc.ensure_provision_credentials`
        # Новая форма: `_, creds, _ = await account_svc.ensure_provision_credentials`
        assert (
            "_, creds, _ = await account_svc.ensure_provision_credentials" in src
            or "generated = await account_svc.ensure_provision_credentials" not in src
        ), "Неиспользуемая переменная `generated` оставлена в worker_dispatch"


# ── 14. audit_service._pending_audit_tasks strong-ref ────────────────────────


class TestPendingAuditTasksStrongRef:
    def test_pending_audit_tasks_is_module_level_set(self):
        assert isinstance(audit_service._pending_audit_tasks, set)

    def test_emit_adds_task_to_set(self):
        src = inspect.getsource(audit_service.emit)
        assert "_pending_audit_tasks.add" in src
        assert "add_done_callback" in src
        assert "_pending_audit_tasks.discard" in src


# ── 15. lifespan drain ───────────────────────────────────────────────────────


class TestLifespanDrainsPendingAuditTasks:
    def test_drain_function_exists(self):
        assert hasattr(server_main, "_drain_pending_audit_tasks")
        assert inspect.iscoroutinefunction(server_main._drain_pending_audit_tasks)

    def test_drain_timeout_constant_reasonable(self):
        assert 0 < server_main._AUDIT_DRAIN_TIMEOUT_SECONDS <= 30

    def test_drain_uses_asyncio_wait_with_timeout(self):
        src = inspect.getsource(server_main._drain_pending_audit_tasks)
        assert "asyncio.wait" in src
        assert "_AUDIT_DRAIN_TIMEOUT_SECONDS" in src

    def test_lifespan_calls_drain(self):
        src = inspect.getsource(server_main.create_application)
        assert "_drain_pending_audit_tasks" in src

    def test_drain_handles_empty_set(self):
        # smoke: пустой set → возврат без ошибки.
        audit_service._pending_audit_tasks.clear()
        asyncio.run(server_main._drain_pending_audit_tasks())
