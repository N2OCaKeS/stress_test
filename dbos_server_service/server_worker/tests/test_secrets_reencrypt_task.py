"""Periodic task `secrets.reencrypt_lazy` — фоновая ре-шифрация секретов.

После перехода на outbox-pattern контракт изменился:

* worker не вызывает `trigger_secrets_reencrypt_batch`;
* worker зовёт `fetch_secrets_migration_status` (status + outbox-снапшот),
  при необходимости `seed_reencrypt_outbox`, затем
  `claim_reencrypt_outbox_pending` и для каждого row'а
  `finalize_reencrypt_outbox_done`.

Сценарии:

* Скип на disable-флаге.
* Скип когда `RUNNING_TASKS` непуст (high-prio задача в полёте).
* Happy path: remaining=0 & outbox пустой → audit success processed=0.
* remaining>0, outbox пустой → выполняется seed + claim + finalize.
* outbox.pending>0 → seed не зовётся, claim + finalize.
* Transport-failure на status → выход без claim, без exception.
* Transport-failure на finalize_done → finalize_failed вызывается.
* APP_ENV-mismatch → abort + audit failure.
* batch size прокидывается в claim.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


async def _invoke_task() -> None:
    """Дёрнуть task body — учитывая taskiq-wrapping через `original_func`."""
    from src.main import secrets_reencrypt_lazy

    fn = getattr(secrets_reencrypt_lazy, "original_func", secrets_reencrypt_lazy)
    await fn()


@pytest.fixture(autouse=True)
def _clear_running_tasks():
    from src.tasks._runner_state import RUNNING_TASKS

    RUNNING_TASKS.clear()
    yield
    RUNNING_TASKS.clear()


@pytest.fixture(autouse=True)
def _sync_settings_singleton():
    """Гарантируем, что `src.main._settings` и `get_settings()` — один объект.

    Соседние тесты делают `get_settings.cache_clear()`, после чего
    `src.main._settings`, прихваченный на импорте, остаётся указывать на
    старый объект. Фикстура clear'ит кэш, форсит populate и пере-присваивает
    `src.main._settings` к тому же объекту.
    """
    from src import main as worker_main
    from src.core import config as cfg

    cfg.get_settings.cache_clear()
    fresh = cfg.get_settings()
    original = worker_main._settings
    worker_main._settings = fresh
    yield
    worker_main._settings = original
    cfg.get_settings.cache_clear()


class TestSchedulerRegistration:
    def test_task_in_broker(self):
        from src.main import broker

        assert "secrets.reencrypt_lazy" in broker.get_all_tasks()


class TestDisableFlag:
    async def test_disabled_returns_without_calling_status(
        self, monkeypatch, captured_audit
    ):
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", False)
        status_mock = AsyncMock(return_value={"remaining": 1})
        claim_mock = AsyncMock(return_value=[])
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )

        await _invoke_task()

        status_mock.assert_not_called()
        claim_mock.assert_not_called()
        # При disabled мы НЕ должны писать audit (просто silent exit).
        assert captured_audit == []


class TestSkipOnActiveTasks:
    async def test_skips_when_running_tasks_present(
        self, monkeypatch, captured_audit
    ):
        from src.main import _settings
        from src.services import server_service_client
        from src.tasks._runner_state import RUNNING_TASKS

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        RUNNING_TASKS.add("tsk_active_001")

        status_mock = AsyncMock(return_value={"remaining": 5})
        claim_mock = AsyncMock(return_value=[])
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )

        await _invoke_task()

        status_mock.assert_not_called()
        claim_mock.assert_not_called()
        skip_events = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert len(skip_events) == 1
        assert skip_events[0]["details"]["skipped"] is True
        assert skip_events[0]["details"]["reason"] == "active_tasks_present"


class TestHappyPath:
    async def test_seeds_claims_and_finalizes_when_outbox_empty(
        self, monkeypatch, captured_audit
    ):
        """remaining>0 + outbox пустой: seed → claim → finalize_done."""
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "secrets_reencrypt_batch_size", 50)

        status_mock = AsyncMock(return_value={
            "remaining": 100,
            "total": 200,
            "active_version": 2,
            "by_version": {"1": 100, "2": 100},
            "outbox": {"pending": 0, "processing": 0, "done": 0, "failed": 0},
        })
        seed_mock = AsyncMock(return_value={
            "inserted": 100, "scanned": 100, "active_version": 2,
        })
        claim_mock = AsyncMock(return_value=[
            {"id": "rox_a", "entity_type": "server_account",
             "entity_id": "acc_1", "legacy_ciphertext": "v1$n$c", "attempts": 1},
            {"id": "rox_b", "entity_type": "ipmi_controller",
             "entity_id": "ipmi_1", "legacy_ciphertext": "v1$n$c", "attempts": 1},
        ])
        done_mock = AsyncMock(return_value={
            "id": "rox_a", "status": "done", "skipped": False,
        })
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "seed_reencrypt_outbox", seed_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )
        monkeypatch.setattr(
            server_service_client, "finalize_reencrypt_outbox_done", done_mock,
        )

        await _invoke_task()

        seed_mock.assert_awaited_once()
        claim_mock.assert_awaited_once_with(limit=50)
        assert done_mock.await_count == 2

        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert len(tick) == 1
        details = tick[0]["details"]
        assert details["processed"] == 2
        assert details["errors"] == 0
        assert details["claimed"] == 2
        assert details["seeded"] == 100
        assert details["remaining_before"] == 100
        assert details["active_version"] == 2
        assert details["batch_size"] == 50

    async def test_skips_seed_when_outbox_has_pending(
        self, monkeypatch, captured_audit
    ):
        """outbox.pending>0 → seed не зовётся; сразу claim."""
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "secrets_reencrypt_batch_size", 25)

        status_mock = AsyncMock(return_value={
            "remaining": 50,
            "total": 200,
            "active_version": 2,
            "by_version": {"1": 50},
            "outbox": {"pending": 50, "processing": 0, "done": 0, "failed": 0},
        })
        seed_mock = AsyncMock()
        claim_mock = AsyncMock(return_value=[
            {"id": "rox_x", "entity_type": "server_account",
             "entity_id": "acc_x", "legacy_ciphertext": "v1$n$c", "attempts": 1},
        ])
        done_mock = AsyncMock(return_value={
            "id": "rox_x", "status": "done", "skipped": False,
        })
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "seed_reencrypt_outbox", seed_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )
        monkeypatch.setattr(
            server_service_client, "finalize_reencrypt_outbox_done", done_mock,
        )

        await _invoke_task()

        seed_mock.assert_not_called()
        claim_mock.assert_awaited_once_with(limit=25)
        done_mock.assert_awaited_once_with("rox_x")

        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert tick[0]["details"]["seeded"] == 0
        assert tick[0]["details"]["processed"] == 1

    async def test_does_nothing_when_remaining_zero_and_outbox_empty(
        self, monkeypatch, captured_audit
    ):
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)

        status_mock = AsyncMock(return_value={
            "remaining": 0,
            "total": 50,
            "active_version": 2,
            "by_version": {"2": 50},
            "outbox": {"pending": 0, "processing": 0, "done": 0, "failed": 0},
        })
        seed_mock = AsyncMock()
        claim_mock = AsyncMock()
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "seed_reencrypt_outbox", seed_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )

        await _invoke_task()

        seed_mock.assert_not_called()
        claim_mock.assert_not_called()
        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert len(tick) == 1
        assert tick[0]["details"]["processed"] == 0
        assert tick[0]["details"]["remaining"] == 0

    async def test_skipped_finalize_counts_separately(
        self, monkeypatch, captured_audit
    ):
        """finalize_done вернул skipped=True → отдельный счётчик в audit."""
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "secrets_reencrypt_batch_size", 10)

        status_mock = AsyncMock(return_value={
            "remaining": 5,
            "total": 5,
            "active_version": 2,
            "by_version": {"1": 5},
            "outbox": {"pending": 5, "processing": 0, "done": 0, "failed": 0},
        })
        claim_mock = AsyncMock(return_value=[
            {"id": "rox_skip", "entity_type": "server_account",
             "entity_id": "acc_s", "legacy_ciphertext": "v1$n$c", "attempts": 1},
        ])
        done_mock = AsyncMock(return_value={
            "id": "rox_skip", "status": "done", "skipped": True,
        })
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )
        monkeypatch.setattr(
            server_service_client, "finalize_reencrypt_outbox_done", done_mock,
        )

        await _invoke_task()

        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        details = tick[0]["details"]
        assert details["skipped"] == 1
        assert details["processed"] == 0


class TestFailureModes:
    async def test_status_fetch_failure_does_not_raise(
        self, monkeypatch, captured_audit
    ):
        from src.core.exceptions import CredentialFetchError
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)

        async def boom(*_, **__):
            raise CredentialFetchError(
                error_code="SERVER_SERVICE_UNREACHABLE",
                message="boom",
            )

        claim_mock = AsyncMock()
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", boom,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )

        await _invoke_task()

        claim_mock.assert_not_called()

    async def test_finalize_failure_marks_failed(
        self, monkeypatch, captured_audit
    ):
        """finalize_done бросил → finalize_failed зовётся, errors инкрементится."""
        from src.core.exceptions import CredentialFetchError
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)

        status_mock = AsyncMock(return_value={
            "remaining": 1, "total": 1, "active_version": 2,
            "by_version": {"1": 1},
            "outbox": {"pending": 1, "processing": 0, "done": 0, "failed": 0},
        })
        claim_mock = AsyncMock(return_value=[
            {"id": "rox_bad", "entity_type": "server_account",
             "entity_id": "acc_bad", "legacy_ciphertext": "v1$n$c", "attempts": 1},
        ])

        async def boom_done(_id):
            raise CredentialFetchError(
                error_code="SECRETS_OUTBOX_FINALIZE_REJECTED",
                message="crypto",
            )

        failed_mock = AsyncMock(return_value={"id": "rox_bad", "status": "failed"})

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )
        monkeypatch.setattr(
            server_service_client, "finalize_reencrypt_outbox_done", boom_done,
        )
        monkeypatch.setattr(
            server_service_client, "finalize_reencrypt_outbox_failed", failed_mock,
        )

        await _invoke_task()

        failed_mock.assert_awaited_once()
        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert tick[0]["details"]["errors"] == 1
        assert tick[0]["status"] == "warning"

    async def test_claim_failure_does_not_raise(
        self, monkeypatch, captured_audit
    ):
        from src.core.exceptions import CredentialFetchError
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)

        status_mock = AsyncMock(return_value={
            "remaining": 1, "total": 1, "active_version": 2,
            "by_version": {"1": 1},
            "outbox": {"pending": 1, "processing": 0, "done": 0, "failed": 0},
        })

        async def boom_claim(*_, **__):
            raise CredentialFetchError(
                error_code="SECRETS_OUTBOX_CLAIM_REJECTED",
                message="db down",
            )

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", boom_claim,
        )

        # Не должно бросить.
        await _invoke_task()

        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert tick == []

    async def test_unexpected_exception_on_status_swallowed(
        self, monkeypatch
    ):
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)

        async def boom(*_, **__):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", boom,
        )

        await _invoke_task()


class TestAppEnvGuard:
    async def test_mismatch_aborts_tick(self, monkeypatch, captured_audit):
        """server_service отдал чужой APP_ENV → claim не вызывается, audit failure."""
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "app_env", "staging")

        status_mock = AsyncMock(return_value={
            "remaining": 10,
            "total": 10,
            "active_version": 2,
            "by_version": {"1": 10},
            "app_env": "production",
            "outbox": {"pending": 0, "processing": 0, "done": 0, "failed": 0},
        })
        claim_mock = AsyncMock()
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )

        await _invoke_task()

        status_mock.assert_awaited_once()
        claim_mock.assert_not_called()
        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert len(tick) == 1
        assert tick[0]["status"] == "failure"
        assert tick[0]["details"]["reason"] == "app_env_mismatch"
        assert tick[0]["details"]["worker_app_env"] == "staging"
        assert tick[0]["details"]["server_service_app_env"] == "production"

    async def test_match_case_insensitive_proceeds(self, monkeypatch, captured_audit):
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "app_env", "Production")

        status_mock = AsyncMock(return_value={
            "remaining": 5,
            "total": 5,
            "active_version": 2,
            "by_version": {"1": 5},
            "app_env": "production",
            "outbox": {"pending": 5, "processing": 0, "done": 0, "failed": 0},
        })
        claim_mock = AsyncMock(return_value=[])
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )

        await _invoke_task()

        claim_mock.assert_awaited_once()

    async def test_missing_app_env_in_status_proceeds(self, monkeypatch, captured_audit):
        """Старый server_service без `app_env` в ответе — guard молчит, тик идёт штатно."""
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "app_env", "production")

        status_mock = AsyncMock(return_value={
            "remaining": 5,
            "total": 5,
            "active_version": 2,
            "by_version": {"1": 5},
            "outbox": {"pending": 5, "processing": 0, "done": 0, "failed": 0},
        })
        claim_mock = AsyncMock(return_value=[])
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )

        await _invoke_task()

        claim_mock.assert_awaited_once()


class TestBatchSizeWiring:
    async def test_passes_configured_batch_size(self, monkeypatch):
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "secrets_reencrypt_batch_size", 250)

        status_mock = AsyncMock(return_value={
            "remaining": 500, "total": 1000, "active_version": 2,
            "by_version": {"1": 500},
            "outbox": {"pending": 500, "processing": 0, "done": 0, "failed": 0},
        })
        claim_mock = AsyncMock(return_value=[])
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "claim_reencrypt_outbox_pending", claim_mock,
        )

        await _invoke_task()

        claim_mock.assert_awaited_once_with(limit=250)
