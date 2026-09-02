"""Юнит-тесты: `audit_service.emit` держит strong-ref на фоновую отправку.

Без сильной ссылки `loop.create_task(...)` отдаёт task только через weakref
event loop'а — GC может собрать корутину до того, как она ударит в
loging_service, и payload теряется молча. Зеркаль фикс из
`server_worker/src/tasks/_runner.py` (`_RETRY_TASKS`).
"""

import asyncio

import pytest

from src.services import audit_service


@pytest.mark.asyncio
async def test_emit_registers_task_in_strong_ref_set(monkeypatch):
    """Один `emit` с настроенным logging_url → ровно один task в `_EMIT_TASKS`.

    Реальную доставку не делаем — корутина `_send_to_logging_service`
    подменена на медленный sleep, чтобы task оставался running и попадал
    в проверку до того, как done-callback его уберёт.
    """
    audit_service._EMIT_TASKS.clear()

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_send(payload, url, api_key):
        started.set()
        await release.wait()

    monkeypatch.setattr(audit_service, "_send_to_logging_service", fake_send)

    settings = audit_service.get_settings()
    monkeypatch.setattr(settings, "logging_service_url", "http://loging.test", raising=False)
    monkeypatch.setattr(settings, "logging_service_api_key", "k", raising=False)

    audit_service.emit("user.login")
    await started.wait()

    assert len(audit_service._EMIT_TASKS) == 1
    task = next(iter(audit_service._EMIT_TASKS))
    assert not task.done()

    release.set()
    await task
    # done-callback должен вычистить set.
    assert audit_service._EMIT_TASKS == set()


@pytest.mark.asyncio
async def test_emit_task_set_drained_on_completion(monkeypatch):
    """Несколько emit'ов: после await всех — set пустой."""
    audit_service._EMIT_TASKS.clear()

    async def fake_send(payload, url, api_key):
        await asyncio.sleep(0)

    monkeypatch.setattr(audit_service, "_send_to_logging_service", fake_send)

    settings = audit_service.get_settings()
    monkeypatch.setattr(settings, "logging_service_url", "http://loging.test", raising=False)
    monkeypatch.setattr(settings, "logging_service_api_key", "k", raising=False)

    for i in range(5):
        audit_service.emit("user.login", request_id=f"req_{i}")

    tasks = list(audit_service._EMIT_TASKS)
    assert len(tasks) == 5
    await asyncio.gather(*tasks)
    assert audit_service._EMIT_TASKS == set()
