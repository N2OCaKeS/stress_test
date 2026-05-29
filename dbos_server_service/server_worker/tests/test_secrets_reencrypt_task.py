"""Periodic task `secrets.reencrypt_lazy` — фоновая ре-шифрация секретов.

Тест-сценарии:

* Скип на disable-флаге (`SECRETS_REENCRYPT_ENABLED=false`).
* Скип когда `RUNNING_TASKS` не пуст (high-prio задача в полёте).
* Happy path: status returns remaining>0 → batch вызывается.
* `remaining=0` → batch НЕ вызывается, audit success processed=0.
* Transport-failure на status → выход без batch, без exception.
* Transport-failure на batch → audit о failed-batch не пишется,
  task не падает.
* Audit-tick содержит processed/errors/remaining/active_version.

Реальный HTTP к server_service замочен через monkeypatch'и:
`server_service_client.fetch_secrets_migration_status` и
`server_service_client.trigger_secrets_reencrypt_batch` подменяются на
async-stub'ы.
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
    """Каждый тест стартует с пустым `RUNNING_TASKS`."""
    from src.tasks._runner_state import RUNNING_TASKS

    RUNNING_TASKS.clear()
    yield
    RUNNING_TASKS.clear()


@pytest.fixture(autouse=True)
def _sync_settings_singleton():
    """Гарантируем, что `src.main._settings` и `get_settings()` — один объект.

    Соседние тесты (`test_secrets_reencrypt_task.py` и инвентаризационные)
    дёргают `get_settings.cache_clear()`, после чего `get_settings()` возвращает
    **новый** Settings-инстанс, а `src.main._settings`, прихваченный на импорте
    `src/main.py`, остаётся указывать на старый. Наши тесты делают
    `monkeypatch.setattr(_settings, ...)`,
    но task body внутри `secrets.reencrypt_lazy` зовёт `get_settings()` — и
    видит другой объект, где монки нет.

    Фикстура clear'ит кэш, форсит populate и пере-присваивает `src.main._settings`
    к тому же объекту. Teardown — симметричный clear, чтобы не утаскивать
    свой инстанс в следующий тестовый модуль.
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
        batch_mock = AsyncMock(return_value={"processed": 1, "errors": 0})
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        await _invoke_task()

        status_mock.assert_not_called()
        batch_mock.assert_not_called()
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
        batch_mock = AsyncMock(return_value={"processed": 5, "errors": 0})
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        await _invoke_task()

        status_mock.assert_not_called()
        batch_mock.assert_not_called()
        # Audit-tick всё-таки эмитится со skipped=True — оператор должен
        # видеть, что worker отрабатывал, но уступил.
        skip_events = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert len(skip_events) == 1
        assert skip_events[0]["details"]["skipped"] is True
        assert skip_events[0]["details"]["reason"] == "active_tasks_present"


class TestHappyPath:
    async def test_calls_batch_when_remaining_positive(
        self, monkeypatch, captured_audit
    ):
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "secrets_reencrypt_batch_size", 50)

        status_mock = AsyncMock(return_value={
            "remaining": 100,
            "total": 200,
            "active_version": 2,
            "by_version": {"1": 100, "2": 100},
        })
        batch_mock = AsyncMock(return_value={"processed": 50, "errors": 0})
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        await _invoke_task()

        status_mock.assert_awaited_once()
        batch_mock.assert_awaited_once_with(50)
        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert len(tick) == 1
        details = tick[0]["details"]
        assert details["processed"] == 50
        assert details["errors"] == 0
        assert details["remaining_before"] == 100
        assert details["active_version"] == 2
        assert details["batch_size"] == 50

    async def test_does_not_call_batch_when_remaining_zero(
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
        })
        batch_mock = AsyncMock()
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        await _invoke_task()

        status_mock.assert_awaited_once()
        batch_mock.assert_not_called()
        # Audit о «done» с processed=0 должен быть, чтобы операторская
        # дашборда видела «работа доехала».
        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert len(tick) == 1
        assert tick[0]["details"]["processed"] == 0
        assert tick[0]["details"]["remaining"] == 0


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

        batch_mock = AsyncMock()
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", boom,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        # Не должно бросить — periodic-loop устойчив к транзиентным ошибкам.
        await _invoke_task()

        batch_mock.assert_not_called()

    async def test_batch_failure_does_not_raise(
        self, monkeypatch, captured_audit
    ):
        from src.core.exceptions import CredentialFetchError
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)

        status_mock = AsyncMock(return_value={
            "remaining": 10, "total": 10, "active_version": 2, "by_version": {"1": 10},
        })

        async def boom_batch(_limit):
            raise CredentialFetchError(
                error_code="SECRETS_REENCRYPT_REJECTED",
                message="db down",
            )

        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", boom_batch,
        )

        # Не должно бросить.
        await _invoke_task()

        status_mock.assert_awaited_once()
        # Audit-tick про неудачный batch не пишем — иначе одна порча льёт
        # лог. Об ошибке оператор узнаёт по WARNING-логу.
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

        # Periodic-task не должен пробрасывать наружу — иначе scheduler-loop
        # сломается на одной ошибке.
        await _invoke_task()


class TestAppEnvGuard:
    async def test_mismatch_aborts_tick(self, monkeypatch, captured_audit):
        """server_service отдал чужой APP_ENV → batch не вызывается, audit failure."""
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
        })
        batch_mock = AsyncMock(return_value={"processed": 10, "errors": 0})
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        await _invoke_task()

        status_mock.assert_awaited_once()
        batch_mock.assert_not_called()
        tick = [e for e in captured_audit if e["action"] == "secrets.reencrypt_tick"]
        assert len(tick) == 1
        assert tick[0]["status"] == "failure"
        assert tick[0]["details"]["reason"] == "app_env_mismatch"
        assert tick[0]["details"]["worker_app_env"] == "staging"
        assert tick[0]["details"]["server_service_app_env"] == "production"

    async def test_match_case_insensitive_proceeds(self, monkeypatch, captured_audit):
        """APP_ENV сравниваем без учёта регистра — Production==production."""
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
        })
        batch_mock = AsyncMock(return_value={"processed": 5, "errors": 0})
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        await _invoke_task()

        batch_mock.assert_awaited_once()

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
        })
        batch_mock = AsyncMock(return_value={"processed": 5, "errors": 0})
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        await _invoke_task()

        batch_mock.assert_awaited_once()


class TestBatchSizeWiring:
    async def test_passes_configured_batch_size(self, monkeypatch):
        from src.main import _settings
        from src.services import server_service_client

        monkeypatch.setattr(_settings, "secrets_reencrypt_enabled", True)
        monkeypatch.setattr(_settings, "secrets_reencrypt_batch_size", 250)

        status_mock = AsyncMock(return_value={
            "remaining": 500, "total": 1000, "active_version": 2, "by_version": {"1": 500},
        })
        batch_mock = AsyncMock(return_value={"processed": 250, "errors": 0})
        monkeypatch.setattr(
            server_service_client, "fetch_secrets_migration_status", status_mock,
        )
        monkeypatch.setattr(
            server_service_client, "trigger_secrets_reencrypt_batch", batch_mock,
        )

        await _invoke_task()

        batch_mock.assert_awaited_once_with(250)
