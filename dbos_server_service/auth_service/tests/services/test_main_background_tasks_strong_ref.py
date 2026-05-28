"""Юнит-тест: lifespan-startup кладёт `_startup_sequence`-task в
`_BACKGROUND_TASKS`, удерживая strong-ref до завершения.

Раньше `asyncio.ensure_future(asyncio.to_thread(_startup_sequence))` уезжал
без strong-ref'а; под GC pressure (большая startup-куча, gunicorn worker
respawn) task мог быть собран до того, как `register_events` доедет до
loging_service. Зеркаль фикс из worker'а (`_RETRY_TASKS`).
"""

import asyncio
import threading

import pytest

from src import main as main_mod


@pytest.mark.asyncio
async def test_lifespan_strong_refs_startup_task(monkeypatch):
    """`_BACKGROUND_TASKS` содержит startup-task пока `_startup_sequence`
    ещё работает; после завершения — drain через done-callback.
    """
    started = threading.Event()
    release = threading.Event()

    def slow_startup():
        started.set()
        # Блокируемся в worker-thread, пока тест не отпустит.
        release.wait(timeout=5.0)

    monkeypatch.setattr(main_mod, "_startup_sequence", slow_startup)

    async def _noop_bootstrap(_db):
        return None

    async def _empty_db():
        # Пустой async-generator: `async for db in get_db()` не пройдёт ни одной
        # итерации, не дойдём до реального коннекта к Postgres.
        if False:
            yield None

    monkeypatch.setattr(main_mod, "bootstrap_admin", _noop_bootstrap)
    monkeypatch.setattr(main_mod, "get_db", _empty_db)

    main_mod._BACKGROUND_TASKS.clear()
    app = main_mod.create_application()

    async with app.router.lifespan_context(app):
        # Ждём, пока startup-task реально стартанёт поток.
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        assert len(main_mod._BACKGROUND_TASKS) == 1
        task = next(iter(main_mod._BACKGROUND_TASKS))
        assert not task.done()
        release.set()
        await task
        assert main_mod._BACKGROUND_TASKS == set()
