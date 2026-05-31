"""Cap on in-flight `_EMIT_TASKS` set.

Под sustained 429 от loging_service эмит-таски копятся (каждая сидит 2.4s
в backoff'е до drop'а), и неограниченный set может съесть память. Cap
дропает самую старую in-flight task и инкрементит overflow-counter.
"""

import asyncio

import pytest

from src.services import audit_service


@pytest.fixture(autouse=True)
def _reset_state():
    audit_service._EMIT_TASKS.clear()
    audit_service._reset_emit_tasks_overflow_for_tests()
    yield
    audit_service._EMIT_TASKS.clear()
    audit_service._reset_emit_tasks_overflow_for_tests()


@pytest.mark.asyncio
async def test_emit_tasks_overflow_drops_oldest(monkeypatch):
    """При len(_EMIT_TASKS) >= cap: oldest cancel'ится, counter растёт."""
    # Подменяем cap на маленькое значение, чтобы не плодить тысячу task.
    monkeypatch.setattr(audit_service, "_EMIT_TASKS_MAX", 3)

    release = asyncio.Event()

    async def fake_send(payload, url, api_key):
        # Висим до конца теста, чтобы все таски сидели в running.
        try:
            await release.wait()
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(audit_service, "_send_to_logging_service", fake_send)

    settings = audit_service.get_settings()
    monkeypatch.setattr(settings, "logging_service_url", "http://loging.test", raising=False)
    monkeypatch.setattr(settings, "logging_service_api_key", "k", raising=False)

    # Первые три emit'а — set заполнен ровно до cap'а.
    for i in range(3):
        audit_service.emit("user.login", request_id=f"req_{i}")
    # Дать task'ам стартовать, чтобы они попали в set.
    await asyncio.sleep(0)
    assert len(audit_service._EMIT_TASKS) == 3
    assert audit_service.get_emit_tasks_overflow_total() == 0

    # Четвёртый emit: oldest должен быть cancel'нут, counter += 1, set
    # остаётся в пределах cap'а (size = cap, oldest заменён на новый).
    audit_service.emit("user.login", request_id="req_overflow_1")
    await asyncio.sleep(0)
    assert audit_service.get_emit_tasks_overflow_total() == 1
    assert len(audit_service._EMIT_TASKS) <= 3

    # Ещё один — counter += 1.
    audit_service.emit("user.login", request_id="req_overflow_2")
    await asyncio.sleep(0)
    assert audit_service.get_emit_tasks_overflow_total() == 2

    # Финал: отпускаем оставшиеся таски, чтобы pytest не ругался на pending.
    release.set()
    # Дать done-callback'ам отработать.
    remaining = list(audit_service._EMIT_TASKS)
    for t in remaining:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_emit_tasks_overflow_quiet_under_cap(monkeypatch):
    """Пока len < cap — overflow counter не растёт."""
    monkeypatch.setattr(audit_service, "_EMIT_TASKS_MAX", 100)

    async def fake_send(payload, url, api_key):
        await asyncio.sleep(0)

    monkeypatch.setattr(audit_service, "_send_to_logging_service", fake_send)

    settings = audit_service.get_settings()
    monkeypatch.setattr(settings, "logging_service_url", "http://loging.test", raising=False)
    monkeypatch.setattr(settings, "logging_service_api_key", "k", raising=False)

    for i in range(10):
        audit_service.emit("user.login", request_id=f"req_{i}")

    await asyncio.gather(*list(audit_service._EMIT_TASKS))
    assert audit_service.get_emit_tasks_overflow_total() == 0
