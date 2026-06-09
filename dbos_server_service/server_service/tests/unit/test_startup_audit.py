"""Unit-тесты: synchronous startup audit sequence.

Покрывает фикс «`service.started` audit без http-context + `asyncio.ensure_future`
race». До фикса:

* `_startup_sequence` крутился через `asyncio.ensure_future(asyncio.to_thread(...))`
  — fire-and-forget; на холодном старте task мог не успеть запланироваться
  до того, как первый запрос пробуждал event-loop, и event «service.started»
  оставался не отправленным.
* В sync-thread (`asyncio.to_thread`) `audit_service.emit` шёл в sync-fallback
  `httpx.post(timeout=2.0)`, мимо pooled `_audit_client` из lifespan.
* `service.started` эмитился с `actor_type="service"` (нестандартно — в
  каталоге audit обычно `actor_type ∈ {user, system}`), без явного
  `actor_id` / `request_id` — поэтому в audit-логе он попадал с актором из
  contextvar (если был) или с `None`.

После фикса:

* `_run_startup_audit_sequence()` — async-функция, await'ится из lifespan
  ДО `yield` (синхронно: lifespan не вернёт управление uvicorn'у пока emit
  не запланирован). При timeout (`asyncio.wait_for=10s`) — WARNING-лог,
  startup продолжается (не блокирует health-pipeline).
* `service.started` эмитится с `actor_type="system"`, `actor_id="server_service"`,
  `request_id="startup-<pid>"`, `username=None`, `department_id=None`.
* `register_events()` (sync httpx) wrapped в `asyncio.to_thread` чтобы не
  блокировать event-loop.

Тесты этого файла НЕ используют `_patch_introspect` autouse (он не нужен —
startup-sequence не делает introspect-вызовы) и работают через
`app.router.lifespan_context(app)` напрямую.
"""

from __future__ import annotations

import logging

import pytest

from src.main import _run_startup_audit_sequence, create_application
from src.services import audit_service


# ── Базовая семантика: emit с system-actor ─────────────────────────────────


@pytest.mark.asyncio
async def test_startup_emits_service_started_with_system_actor(monkeypatch):
    """`_run_startup_audit_sequence` шлёт `service.started` с system-актором.

    Ожидаем: `actor_type="system"`, `actor_id="server_service"`,
    `request_id` начинается с `"startup-"` (PID-suffix). Username/department
    явно `None` — это lifecycle-event, не действие пользователя.
    """
    captured_emits: list[dict] = []

    def fake_emit(action, **kwargs):
        captured_emits.append({"action": action, **kwargs})

    # register_events — sync httpx — заменяем на no-op, чтобы тест не делал сеть.
    monkeypatch.setattr("src.main.register_events", lambda: None)
    # emit — патчим прямо в audit_service (импорт «from src.services import audit_service»
    # тащит attribute, не bind'имый namespace).
    monkeypatch.setattr(audit_service, "emit", fake_emit)

    await _run_startup_audit_sequence()

    # Найден ровно один service.started
    started = [e for e in captured_emits if e["action"] == "service.started"]
    assert len(started) == 1, f"expected exactly one service.started, got {len(started)}"

    ev = started[0]
    assert ev.get("actor_type") == "system", (
        f"service.started should be system-actor, got actor_type={ev.get('actor_type')!r}"
    )
    assert ev.get("actor_id") == "server_service", (
        f"actor_id should be 'server_service', got {ev.get('actor_id')!r}"
    )
    # request_id явно проставлен — нет «утечки» из не-существующего http-context'а
    assert ev.get("request_id", "").startswith("startup-"), (
        f"request_id should start with 'startup-', got {ev.get('request_id')!r}"
    )
    # username/department явно None — startup НЕ от user'а
    assert ev.get("username") is None
    assert ev.get("department_id") is None


@pytest.mark.asyncio
async def test_startup_calls_register_events_once(monkeypatch):
    """`register_events()` вызывается ровно один раз ДО emit'а service.started."""
    call_log: list[str] = []

    def fake_register_events():
        call_log.append("register_events")

    def fake_emit(action, **kwargs):
        call_log.append(f"emit:{action}")

    monkeypatch.setattr("src.main.register_events", fake_register_events)
    monkeypatch.setattr(audit_service, "emit", fake_emit)

    await _run_startup_audit_sequence()

    # Каталог регистрируется ДО эмиссии lifecycle-event'а — иначе
    # loging_service может отбить событие как unknown action.
    assert call_log == ["register_events", "emit:service.started"], call_log


# ── Resilience: сбои не блокируют startup ──────────────────────────────────


@pytest.mark.asyncio
async def test_startup_emit_failure_does_not_break_startup(monkeypatch, capsys):
    """Если emit бросает — startup продолжается, не падает.

    Симулирует недоступность loging_service после lifespan startup. Это
    edge-case, но он покрывает контракт «best-effort» — `service.started`
    может потеряться, но запуск сервиса не должен упасть из-за этого.
    """
    monkeypatch.setattr("src.main.register_events", lambda: None)

    def boom_emit(action, **kwargs):
        raise RuntimeError("simulated loging_service unreachable")

    monkeypatch.setattr(audit_service, "emit", boom_emit)

    # Lifespan ловит исключения внутри try/except + log WARNING.
    # Проверяем именно lifespan-обёртку (а не сам `_run_startup_audit_sequence`,
    # который raise-it'ит наружу). Используем live create_application().
    # caplog не ловит JSON-logging (configure_logging кладёт записи в stdout
    # напрямую через свой handler без propagate). Используем capsys.
    app = create_application()
    # ВАЖНО: lifespan не должен бросить, даже если emit boom'ит.
    async with app.router.lifespan_context(app):
        pass  # lifespan startup пройден — фикс работает.

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "startup audit sequence failed" in combined, (
        f"expected 'startup audit sequence failed' in stdout/stderr; got:\n{combined[-500:]}"
    )


@pytest.mark.asyncio
async def test_startup_register_events_failure_does_not_break_startup(monkeypatch, capsys):
    """Если `register_events()` бросает — startup продолжается."""

    def boom_register():
        raise RuntimeError("simulated catalog registration failure")

    monkeypatch.setattr("src.main.register_events", boom_register)
    # emit оставляем тоже no-op чтобы не было side-effect от него:
    monkeypatch.setattr(audit_service, "emit", lambda *a, **kw: None)

    app = create_application()
    async with app.router.lifespan_context(app):
        pass

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "startup audit sequence failed" in combined, (
        f"expected 'startup audit sequence failed' in stdout/stderr; got:\n{combined[-500:]}"
    )


# ── Race-fix: lifespan действительно ждёт emit (синхронный путь) ───────────


@pytest.mark.asyncio
async def test_lifespan_awaits_startup_sequence_before_yield(monkeypatch):
    """Lifespan не делает yield пока `_run_startup_audit_sequence` не завершится.

    Раньше `asyncio.ensure_future(asyncio.to_thread(_startup_sequence))`
    мог не успеть запланироваться до yield'а. Сейчас — синхронный `await`.

    Проверяем через флаг: emit ставит флаг = True, после lifespan-входа
    (== `yield`) флаг ДОЛЖЕН быть True.
    """
    monkeypatch.setattr("src.main.register_events", lambda: None)

    completion_flag = {"called": False}

    def fake_emit(action, **kwargs):
        if action == "service.started":
            completion_flag["called"] = True

    monkeypatch.setattr(audit_service, "emit", fake_emit)

    app = create_application()
    async with app.router.lifespan_context(app):
        # На этом моменте startup-half lifespan'а уже отдал управление,
        # значит `_run_startup_audit_sequence()` дождался завершения.
        assert completion_flag["called"] is True, (
            "service.started НЕ был эмитнут до yield в lifespan — "
            "race-fix не работает"
        )


@pytest.mark.asyncio
async def test_lifespan_startup_timeout_does_not_block(monkeypatch, capsys):
    """Если sequence висит дольше timeout'а — lifespan продолжается + WARNING.

    Имитируем «зависший» `register_events` (sleep больше timeout'а). Проверяем
    что lifespan стартует за разумное время и логгирует timeout-warning.
    """
    import asyncio
    import time

    # Сильно занижаем timeout для теста — иначе тест займёт >10s.
    monkeypatch.setattr("src.main._STARTUP_AUDIT_TIMEOUT_SECONDS", 0.3)

    def slow_register():
        # Sync sleep внутри to_thread — wait_for на родительском уровне отрубит.
        time.sleep(2.0)

    monkeypatch.setattr("src.main.register_events", slow_register)
    monkeypatch.setattr(audit_service, "emit", lambda *a, **kw: None)

    app = create_application()
    start = time.monotonic()
    async with app.router.lifespan_context(app):
        pass
    elapsed = time.monotonic() - start

    # Должно завершиться примерно за 0.3s (timeout), не за 2.0s.
    assert elapsed < 1.5, (
        f"lifespan startup with timeout=0.3s занял {elapsed:.2f}s — "
        f"timeout не сработал, startup завис на register_events"
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "timed out" in combined, (
        f"expected 'timed out' in stdout/stderr; got:\n{combined[-500:]}"
    )

    # asyncio.TimeoutError должен был быть пойман — этот импорт нужен только
    # для линтера, явная проверка не нужна (если бы он не был пойман, lifespan
    # бы упал, и async with выше бросил бы исключение).
    _ = asyncio
