"""Unit-тесты для shutdown-order и audit-drain.

Раньше lifespan finally закрывал пулы в произвольном порядке. Если в окне
shutdown'а какой-нибудь endpoint успевал шедулить
``audit_service.emit(...)``, task попадал в loop как fire-and-forget, а
pooled ``_audit_client`` мог быть уже закрыт к моменту, когда task
дотягивался до ``client.post(...)``. Pending audit-event терялся в
``httpx.ClientClosedError``.

Текущий контракт shutdown'а:

1. Closing order — `_introspect_client` → **drain audit tasks** →
   `_audit_client`. `_audit_client` закрывается ПОСЛЕДНИМ (после drain'а).
2. ``_drain_pending_audit_tasks()`` итерирует по
   ``audit_service._pending_audit_tasks`` (set in-flight task'ов) и ждёт
   их завершения с таймаутом ``_AUDIT_DRAIN_TIMEOUT_SECONDS = 2.0``.

Тесты ставят `_audit_client = MockTransport`, шедулят несколько emit'ов
прямо перед shutdown'ом lifespan'а, и проверяют:

* все они физически дошли до transport'а ДО того, как `aclose()`
  вернулся;
* `_audit_client` обнулён и закрыт после shutdown'а;
* drain не вешает event-loop, если pending task'ов нет.
"""

from __future__ import annotations

import asyncio

import pytest


# ── _drain_pending_audit_tasks — базовое поведение ──────────────────────────


@pytest.mark.asyncio
async def test_drain_returns_immediately_when_no_pending(monkeypatch):
    """Без pending audit-task'ов drain возвращает мгновенно."""
    from src import main as main_mod
    from src.services import audit_service

    # Сбрасываем set (вдруг тесты выше что-то оставили).
    audit_service._pending_audit_tasks.clear()

    start = asyncio.get_event_loop().time()
    await main_mod._drain_pending_audit_tasks()
    elapsed = asyncio.get_event_loop().time() - start

    # Запас x10 от таймаута: если drain ждал timeout-секунды без причины — это баг.
    assert elapsed < 0.5, f"drain hung for {elapsed:.2f}s without pending tasks"


@pytest.mark.asyncio
async def test_drain_waits_for_pending_audit_tasks(monkeypatch):
    """Pending audit-task'и из `_pending_audit_tasks` дожидаются drain'ом."""
    from src import main as main_mod
    from src.services import audit_service

    audit_service._pending_audit_tasks.clear()

    completed: list[str] = []

    async def fake_audit_send(name: str):
        await asyncio.sleep(0.05)
        completed.append(name)

    # Шедулим 3 audit-task'и через тот же протокол, что и реальный `emit()`:
    # task + добавление в `_pending_audit_tasks` + discard через done-callback.
    loop = asyncio.get_running_loop()
    for i in range(3):
        t = loop.create_task(fake_audit_send(f"http.client_error_{i}"))
        audit_service._pending_audit_tasks.add(t)
        t.add_done_callback(audit_service._pending_audit_tasks.discard)

    # Сразу проверяем — task'и ещё не успели завершиться
    assert len(completed) == 0
    assert len(audit_service._pending_audit_tasks) == 3

    # Drain должен подождать их завершения
    await main_mod._drain_pending_audit_tasks()

    # Все 3 task'и должны были завершиться к моменту возврата drain'а.
    assert len(completed) == 3, (
        f"drain returned with only {len(completed)}/3 audit tasks finished — "
        f"audit events would be lost on shutdown"
    )
    # `_pending_audit_tasks` должен быть пустой — done-callback убрал.
    assert len(audit_service._pending_audit_tasks) == 0


@pytest.mark.asyncio
async def test_drain_does_not_block_on_unrelated_tasks(monkeypatch):
    """Drain игнорирует task'и, не добавленные в `_pending_audit_tasks`.

    Иначе любой long-running background task в loop'е блокировал бы shutdown
    на drain-timeout — это новая категория DoS на shutdown.
    """
    from src import main as main_mod
    from src.services import audit_service

    audit_service._pending_audit_tasks.clear()

    unrelated_completed = {"done": False}
    never_set = asyncio.Event()

    async def some_other_task():
        # Имитируем долгую background-task'у, которая НЕ audit-emit.
        # `Event.wait()` без `set()` ждёт до cancel — короче и надёжнее
        # `asyncio.sleep(10.0)`, который зависнет на flaky cancel.
        await never_set.wait()
        unrelated_completed["done"] = True

    loop = asyncio.get_running_loop()
    bg = loop.create_task(some_other_task())

    try:
        start = loop.time()
        await main_mod._drain_pending_audit_tasks()
        elapsed = loop.time() - start

        # Drain не должен ждать 10-секундную unrelated-task'у.
        assert elapsed < 0.5, f"drain waited {elapsed:.2f}s for unrelated task"
        assert unrelated_completed["done"] is False
    finally:
        bg.cancel()
        try:
            await bg
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_drain_respects_timeout(monkeypatch):
    """Если audit-task висит больше ``_AUDIT_DRAIN_TIMEOUT_SECONDS`` — drain возвращается, не висит вечно.

    k8s SIGTERM → drain → lifespan finally → process exit. terminationGracePeriodSeconds
    ~30s, мы не можем висеть вечно даже на flaky loging_service'е.
    """
    from src import main as main_mod
    from src.services import audit_service

    audit_service._pending_audit_tasks.clear()
    never_set = asyncio.Event()

    async def hung_send():
        # `Event.wait()` без `set()` — висим до cancel'а, аналог `sleep(10)`
        # без риска флэйки cancel'а под лоадом CI.
        await never_set.wait()

    # Сокращаем timeout до 0.3s, чтобы тест не тратил 2 секунды.
    monkeypatch.setattr(main_mod, "_AUDIT_DRAIN_TIMEOUT_SECONDS", 0.3)

    loop = asyncio.get_running_loop()
    hung_task = loop.create_task(hung_send())
    audit_service._pending_audit_tasks.add(hung_task)
    hung_task.add_done_callback(audit_service._pending_audit_tasks.discard)

    try:
        start = loop.time()
        await main_mod._drain_pending_audit_tasks()
        elapsed = loop.time() - start

        # Drain должен вернуться в районе таймаута (0.3-0.5s), а не ждать 10s.
        assert 0.2 < elapsed < 1.5, (
            f"drain elapsed {elapsed:.2f}s — expected near {0.3}s timeout"
        )
    finally:
        hung_task.cancel()
        try:
            await hung_task
        except asyncio.CancelledError:
            pass
        audit_service._pending_audit_tasks.discard(hung_task)


# ── lifespan shutdown order: audit_client закрывается ПОСЛЕ drain'а ─────────


@pytest.mark.asyncio
async def test_lifespan_closes_audit_client_after_drain(monkeypatch):
    """В lifespan finally `_audit_client.aclose()` идёт ПОСЛЕ drain'а pending audit-task'ов.

    Pending audit-event'ы должны успеть отправиться до того, как pool
    закрывается. Иначе они теряются в `ClientClosedError`.

    Тест шедулит audit-task через `emit()` (которая регистрирует task в
    `_pending_audit_tasks`), сразу выходит из lifespan'а — drain должен
    дождаться send'а, и только потом aclose() обнулит pool.
    """
    from src.core import config as config_mod
    from src.main import create_application
    from src.services import audit_service

    monkeypatch.setenv("LOGGING_SERVICE_URL", "http://logging.test.local")
    monkeypatch.setenv("LOGGING_SERVICE_API_KEY", "test-api-key")
    config_mod.get_settings.cache_clear()
    audit_service._pending_audit_tasks.clear()

    # Заглушаем startup audit
    async def _noop_startup() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop_startup)

    events: list[str] = []
    audit_send_finished = {"value": False}

    async def fake_send(payload, url, api_key):
        events.append("send_started")
        await asyncio.sleep(0.05)
        # ИНВАРИАНТ: внутри send'а `_audit_client` ещё должен быть жив.
        client = audit_service._audit_client
        assert client is not None, "audit_client уже None при активном send'е"
        assert client.is_closed is False, (
            "audit_client.is_closed=True во время активного _send_to_logging_service — "
            "shutdown order не соблюдён (drain не дождался send'а)"
        )
        events.append("send_finished")
        audit_send_finished["value"] = True

    monkeypatch.setattr(audit_service, "_send_to_logging_service", fake_send)

    app = create_application()
    async with app.router.lifespan_context(app):
        # Шедулим audit emit через настоящий `emit()` — он сам положит task
        # в `_pending_audit_tasks` (и потом drain его подберёт).
        audit_service.emit(
            "http.client_error",
            status="failure",
            allowed=False,
            details={"path": "/test"},
        )
        # Sanity: emit действительно положил task в трекинг.
        assert len(audit_service._pending_audit_tasks) >= 1

    # После выхода из контекста (shutdown отработал):
    assert audit_send_finished["value"] is True, (
        f"Pending audit emit не отправился до shutdown'а — событие потеряно. "
        f"events={events}"
    )
    assert audit_service._audit_client is None
    assert events == ["send_started", "send_finished"], (
        f"Неожиданная последовательность shutdown events: {events}"
    )


@pytest.mark.asyncio
async def test_lifespan_completes_clean_shutdown(monkeypatch):
    """Lifespan shutdown отрабатывает без ошибок (closing-order контракт).

    Кэша introspect нет. Проверяем, что startup/shutdown цикл проходит чисто.
    """
    from src.core import config as config_mod
    from src.main import create_application

    monkeypatch.setenv("LOGGING_SERVICE_URL", "http://logging.test.local")
    config_mod.get_settings.cache_clear()

    async def _noop_startup() -> None:
        return None
    monkeypatch.setattr("src.main._run_startup_audit_sequence", _noop_startup)

    app = create_application()
    async with app.router.lifespan_context(app):
        pass
