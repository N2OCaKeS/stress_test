"""Unit-тесты: module-level `_audit_client` переиспользуется вместо
`httpx.AsyncClient` per-call (FD-amplification фикс).

Зеркало `server_service/tests/unit/test_audit_and_auth_client_pool.py`:
`audit_service.emit` под login flood / slowloris больше не создаёт новый
AsyncClient на каждый emit, TLS-handshake амортизируется через pool.

Тесты НЕ запускают сетевые roundtrip'ы — `MockTransport` имитирует
loging_service. Проверяется invariant: при заранее поднятом pooled-client'е
`httpx.AsyncClient.__init__` НЕ должен быть вызван внутри hot-path функций.
"""

from __future__ import annotations

import httpx
import pytest

from src.services import audit_service


@pytest.mark.asyncio
async def test_audit_emit_reuses_pooled_client(monkeypatch):
    """30 последовательных `_send_to_logging_service` → 0 новых `httpx.AsyncClient`.

    Когда `_audit_client` уже инициализирован (как в lifespan startup),
    pooled-путь должен переиспользовать одну TCP-сессию. Создание нового
    клиента — индикатор regression'а к per-call поведению.
    """
    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        requests_seen.append({
            "url": str(request.url),
            "body": _json.loads(request.content.decode()),
            "auth": request.headers.get("Authorization"),
        })
        return httpx.Response(202, json={"accepted": True})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )

    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    # Счётчик создаваемых клиентов. Если фикс работает — НИ ОДНОГО внутри
    # _send_to_logging_service (pooled путь).
    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(audit_service.httpx, "AsyncClient", counting_factory)

    try:
        # 30 последовательных вызовов — symuлирует login flood
        # (30 successful login'ов → 30 audit-emit'ов user.login).
        for i in range(30):
            await audit_service._send_to_logging_service(
                {"action": f"user.login", "request_id": f"req_{i:03d}"},
                "http://loging-mock",
                "test-audit-key",
            )
        assert len(requests_seen) == 30
        # Ключевой инвариант: НИ ОДНОГО нового httpx.AsyncClient.
        assert new_client_counter["created"] == 0, (
            f"pooled path leaked: {new_client_counter['created']} новых клиентов"
        )
        # Sanity: auth header передан, body доехал.
        assert all(r["auth"] == "Bearer test-audit-key" for r in requests_seen)
        assert requests_seen[0]["url"].endswith("/api/logging/v1/events")
    finally:
        await pooled.aclose()


@pytest.mark.asyncio
async def test_audit_send_falls_back_to_per_call_when_pool_uninitialised(monkeypatch):
    """Когда `_audit_client is None` (вне lifespan) — fallback создаёт per-call.

    Обязательное поведение для unit-тестов, импортирующих модуль до старта app.
    """
    monkeypatch.setattr(audit_service, "_audit_client", None)

    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"accepted": True})

    transport = httpx.MockTransport(handler)

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        kwargs.pop("transport", None)
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(audit_service.httpx, "AsyncClient", counting_factory)

    # Два emit'а — два клиента должно быть создано (fallback per-call).
    await audit_service._send_to_logging_service(
        {"action": "x"}, "http://loging-mock", "key"
    )
    await audit_service._send_to_logging_service(
        {"action": "y"}, "http://loging-mock", "key"
    )
    assert new_client_counter["created"] == 2


@pytest.fixture
def noop_startup_env(monkeypatch):
    """Заглушить три startup-зависимости lifespan'а: _startup_sequence,
    bootstrap_admin, get_db. Без этого каждый test-кейс повторял один
    и тот же блок патчей.
    """
    async def _noop_startup() -> None:
        return None

    async def _noop_bootstrap(db):
        return None

    async def _empty_db():
        # пустой generator — async for x in get_db(): не сделает ни одной итерации
        if False:
            yield None

    monkeypatch.setattr("src.main._startup_sequence", _noop_startup)
    monkeypatch.setattr("src.main.bootstrap_admin", _noop_bootstrap)
    monkeypatch.setattr("src.main.get_db", _empty_db)
    return monkeypatch


@pytest.mark.asyncio
async def test_lifespan_initialises_audit_pool(monkeypatch, noop_startup_env):
    """После старта lifespan `_audit_client` — живой AsyncClient."""
    from src.core import config as config_mod
    from src.main import create_application

    # Задаём logging_service_url через env — audit-pool conditional skip
    # если url пустой; для теста нужно явно непустое значение.
    monkeypatch.setenv("LOGGING_SERVICE_URL", "http://logging.test.local")
    monkeypatch.setenv("LOGGING_SERVICE_API_KEY", "test-key")
    config_mod.get_settings.cache_clear()

    # Сбрасываем module-level state перед стартом.
    audit_service._audit_client = None

    app = create_application()
    async with app.router.lifespan_context(app):
        assert audit_service._audit_client is not None
        assert isinstance(audit_service._audit_client, httpx.AsyncClient)
        assert audit_service._audit_client.is_closed is False

    # После shutdown — обнулён и закрыт.
    assert audit_service._audit_client is None


@pytest.mark.asyncio
async def test_lifespan_shutdown_closes_audit_pool(monkeypatch, noop_startup_env):
    """`aclose()` действительно вызывается на shutdown для пула."""
    from src.core import config as config_mod
    from src.main import create_application

    monkeypatch.setenv("LOGGING_SERVICE_URL", "http://logging.test.local")
    monkeypatch.setenv("LOGGING_SERVICE_API_KEY", "test-key")
    config_mod.get_settings.cache_clear()

    audit_service._audit_client = None

    captured: dict = {}

    app = create_application()
    async with app.router.lifespan_context(app):
        captured["audit"] = audit_service._audit_client
        assert captured["audit"] is not None

    assert captured["audit"].is_closed is True


@pytest.mark.asyncio
async def test_lifespan_skips_audit_pool_when_logging_url_empty(monkeypatch, noop_startup_env):
    """Если LOGGING_SERVICE_URL пустой — `_audit_client` остаётся None.

    Это dev-сценарий без loging_service: audit-emit'ы идут в локальный лог,
    pooled client не нужен.
    """
    from src.core import config as config_mod
    from src.main import create_application

    monkeypatch.delenv("LOGGING_SERVICE_URL", raising=False)
    monkeypatch.delenv("LOGGING_SERVICE_API_KEY", raising=False)
    config_mod.get_settings.cache_clear()

    audit_service._audit_client = None

    app = create_application()
    async with app.router.lifespan_context(app):
        assert audit_service._audit_client is None

    assert audit_service._audit_client is None


@pytest.mark.asyncio
async def test_lifespan_shutdown_drains_inflight_emit_tasks(monkeypatch, noop_startup_env):
    """`lifespan.finally` обязан дождаться `_EMIT_TASKS` ДО `aclose()` пула.

    Без drain'а гонка: emit-таска прочитала `_audit_client` и ушла в `await
    client.post(...)`; shutdown тем временем закрывает клиент и
    `httpx.ClientClosedError` рушит таску → событие потеряно. Тест
    подсовывает медленную emit-таску в `_EMIT_TASKS` и убеждается, что к
    моменту выхода из lifespan она завершена.
    """
    import asyncio as _asyncio

    from src.core import config as config_mod
    from src.main import create_application

    monkeypatch.setenv("LOGGING_SERVICE_URL", "http://logging.test.local")
    monkeypatch.setenv("LOGGING_SERVICE_API_KEY", "test-key")
    config_mod.get_settings.cache_clear()

    audit_service._audit_client = None
    audit_service._EMIT_TASKS.clear()

    app = create_application()
    completed = {"done": False}
    slow_done = _asyncio.Event()

    async with app.router.lifespan_context(app):
        # Имитируем уже-стартовавшую emit-таску: она «отправляется» в loging
        # и в этот момент shutdown решает закрыть пул. До фикса pool.aclose()
        # успел бы пройти, и `client.post` упал бы с ClientClosedError.
        # Используем Event вместо sleep — детерминированно и без timing-flake.
        async def _slow_emit():
            # Минимальная пауза для cooperative переключения, но без real-sleep.
            await _asyncio.sleep(0)
            completed["done"] = True
            slow_done.set()

        task = _asyncio.create_task(_slow_emit())
        audit_service._EMIT_TASKS.add(task)
        task.add_done_callback(audit_service._EMIT_TASKS.discard)

    # После выхода из lifespan-context shutdown должен был дождаться таски.
    assert completed["done"] is True, (
        "shutdown не дренировал in-flight emit-таску до aclose()"
    )
    assert slow_done.is_set()


@pytest.mark.asyncio
async def test_parallel_emits_share_one_pooled_client(monkeypatch):
    """30 параллельных emit'ов используют один pool, не создают 30 клиентов.

    Симулирует пиковую нагрузку (login flood / refresh storm) с реальной
    параллельностью через `asyncio.gather`.
    """
    import asyncio

    requests_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(str(request.url))
        return httpx.Response(202, json={"ok": True})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )

    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    new_client_counter = {"created": 0}
    real_async_client = httpx.AsyncClient

    def counting_factory(*args, **kwargs):
        new_client_counter["created"] += 1
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(audit_service.httpx, "AsyncClient", counting_factory)

    try:
        await asyncio.gather(*[
            audit_service._send_to_logging_service(
                {"action": "user.login", "i": i},
                "http://loging-mock",
                "k",
            )
            for i in range(30)
        ])
        assert len(requests_seen) == 30
        # Ни одного нового AsyncClient — все 30 emit'ов через pool.
        assert new_client_counter["created"] == 0
    finally:
        await pooled.aclose()
