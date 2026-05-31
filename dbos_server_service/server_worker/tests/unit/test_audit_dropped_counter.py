"""Unit-тесты монотонного счётчика `_audit_dropped_no_api_key`.

Каждый emit() при пустом LOGGING_SERVICE_API_KEY инкрементирует счётчик.
Тесты проверяют:
* счётчик растёт на 1 за каждый вызов emit с пустым ключом;
* несколько последовательных вызовов накапливаются;
* `get_dropped_no_api_key_total()` возвращает текущее значение;
* `_reset_dropped_counter_for_tests()` сбрасывает к 0 (test helper).
"""

from __future__ import annotations

import pytest

from src.services import audit_client
from src.services.audit_client import AuditEmitError, get_dropped_no_api_key_total


class _NoKeySettings:
    logging_service_url = "http://logging.test"
    logging_service_api_key = ""
    worker_bot_token = ""
    http_request_timeout_seconds = 5.0


class _FakePoolClient:
    async def post(self, *a, **kw):
        raise AssertionError("HTTP should not be called when api_key is empty")


@pytest.fixture(autouse=True)
def _reset():
    audit_client._reset_dropped_counter_for_tests()
    yield
    audit_client._reset_dropped_counter_for_tests()


@pytest.fixture
def no_key_env(monkeypatch):
    monkeypatch.setattr("src.services.audit_client.get_settings", lambda: _NoKeySettings())
    monkeypatch.setattr(
        "src.services.audit_client.get_audit_client", lambda: _FakePoolClient(),
    )


class TestDroppedCounterIncrement:
    async def test_single_emit_increments_by_one(self, no_key_env):
        before = get_dropped_no_api_key_total()
        with pytest.raises(AuditEmitError):
            await audit_client.emit("x.y")
        assert get_dropped_no_api_key_total() == before + 1

    async def test_multiple_emits_accumulate(self, no_key_env):
        before = get_dropped_no_api_key_total()
        for _ in range(5):
            with pytest.raises(AuditEmitError):
                await audit_client.emit("x.y")
        assert get_dropped_no_api_key_total() == before + 5

    async def test_counter_zero_after_reset(self, no_key_env):
        with pytest.raises(AuditEmitError):
            await audit_client.emit("x.y")
        assert get_dropped_no_api_key_total() > 0
        audit_client._reset_dropped_counter_for_tests()
        assert get_dropped_no_api_key_total() == 0

    async def test_counter_not_incremented_on_successful_emit(self, monkeypatch):
        class _GoodSettings:
            logging_service_url = "http://logging.test"
            logging_service_api_key = "valid-key"
            worker_bot_token = ""
            http_request_timeout_seconds = 5.0

        class _OkClient:
            async def post(self, url, json=None, headers=None):
                class _R:
                    status_code = 201
                return _R()

        monkeypatch.setattr("src.services.audit_client.get_settings", lambda: _GoodSettings())
        monkeypatch.setattr(
            "src.services.audit_client.get_audit_client", lambda: _OkClient(),
        )

        before = get_dropped_no_api_key_total()
        await audit_client.emit("x.y")
        assert get_dropped_no_api_key_total() == before  # не инкрементился

    async def test_different_actions_each_increment(self, no_key_env):
        before = get_dropped_no_api_key_total()
        for action in ("action.a", "action.b", "action.c"):
            with pytest.raises(AuditEmitError):
                await audit_client.emit(action)
        assert get_dropped_no_api_key_total() == before + 3
