"""Coverage для трёх startup-путей, которые до этого жили без тестов:

1. ``bootstrap_admin`` эмитит audit ``user.create`` с ``reason=bootstrap_seed``
   при первом наполнении пустой БД.
2. ``engine.dispose()`` действительно вызывается в lifespan-shutdown
   (после http_pool.aclose_all и drain'а emit-тасок).
3. ``register_events`` корректно отрабатывает 5xx → 200 retry: один аудит-катаг
   уходит, повторных публикаций не плодит.
"""

from __future__ import annotations

import httpx
import pytest

from src.services import audit_events, audit_service, bootstrap_service


# ── 1. bootstrap_admin emit ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_admin_emits_user_create_with_bootstrap_seed_reason(
    db, monkeypatch,
):
    """`bootstrap_service.bootstrap_admin` на пустой БД эмитит
    `user.create` audit с `reason=bootstrap_seed` и `actor_type=service`.
    """
    captured: list[dict] = []

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})

    monkeypatch.setattr(bootstrap_service.audit_service, "emit", _spy)

    # ENV-настройки для bootstrap'а: дефолтные SECRET_KEY/DATABASE_URL уже
    # выставлены в conftest. Здесь явно ставим bootstrap-юзера через settings.
    from src.core import config as config_mod
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "boot_admin_emit_check")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Bootstrap1234!")
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "boot@example.com")
    config_mod.get_settings.cache_clear()

    # Подменяем UserRepository.count, чтобы bootstrap_admin поверил, что БД
    # пуста (на самом деле там seed e2e_admin). count=0 — путь создания.
    from src.repositories import users as users_repo_mod

    async def _zero(self):
        return 0

    monkeypatch.setattr(users_repo_mod.UserRepository, "count", _zero)

    await bootstrap_service.bootstrap_admin(db)

    seed_events = [e for e in captured if e["action"] == "user.create"]
    assert len(seed_events) == 1, captured
    ev = seed_events[0]
    assert ev["actor_id"] == "bootstrap"
    assert ev["actor_type"] == "service"
    assert ev["status"] == "success"
    assert ev["allowed"] is True
    assert ev["details"]["reason"] == "bootstrap_seed"
    assert ev["details"]["username"] == "boot_admin_emit_check"


@pytest.mark.asyncio
async def test_bootstrap_admin_noop_when_users_present_emits_nothing(
    db, monkeypatch,
):
    """При непустой БД bootstrap_admin — no-op, никаких audit-emit'ов."""
    captured: list[dict] = []

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action})

    monkeypatch.setattr(bootstrap_service.audit_service, "emit", _spy)

    from src.core import config as config_mod
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "boot_admin_noop")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Bootstrap1234!")
    config_mod.get_settings.cache_clear()

    # count > 0 — путь раннего выхода.
    from src.repositories import users as users_repo_mod

    async def _nonzero(self):
        return 5

    monkeypatch.setattr(users_repo_mod.UserRepository, "count", _nonzero)

    await bootstrap_service.bootstrap_admin(db)

    assert captured == []


# ── 2. engine.dispose() в lifespan-shutdown ──────────────────────────────────


@pytest.mark.asyncio
async def test_lifespan_shutdown_calls_engine_dispose(monkeypatch):
    """Lifespan-shutdown должен дёрнуть `engine.dispose()` (последним, после
    aclose_all/drain). Если регрессия — pool удерживает FD'шники в dev-reload.
    """
    from src.core import config as config_mod
    from src.main import create_application
    import src.main as main_mod

    monkeypatch.delenv("LOGGING_SERVICE_URL", raising=False)
    monkeypatch.delenv("LOGGING_SERVICE_API_KEY", raising=False)
    config_mod.get_settings.cache_clear()

    async def _noop_startup() -> None:
        return None

    async def _noop_bootstrap(d):
        return None

    async def _empty_db():
        if False:
            yield None

    monkeypatch.setattr("src.main._startup_sequence", _noop_startup)
    monkeypatch.setattr("src.main.bootstrap_admin", _noop_bootstrap)
    monkeypatch.setattr("src.main.get_db", _empty_db)

    dispose_calls = {"count": 0}

    # AsyncEngine.dispose — read-only слот; патчим на уровне модуля src.main,
    # подменив сам объект engine на прокси-обёртку. Прокси проксирует всё
    # к реальному engine, но подменяет dispose на spy без real-close
    # (pool ещё нужен test-conftest'у после lifespan).
    real_engine = main_mod.engine

    class _EngineProxy:
        def __getattr__(self, name):
            return getattr(real_engine, name)

        async def dispose(self):
            dispose_calls["count"] += 1
            return None

    monkeypatch.setattr(main_mod, "engine", _EngineProxy())

    audit_service._audit_client = None

    app = create_application()
    async with app.router.lifespan_context(app):
        pass

    assert dispose_calls["count"] == 1, (
        f"engine.dispose должен быть вызван ровно один раз на shutdown, "
        f"got {dispose_calls['count']}"
    )


# ── 3. register_events: 5xx retry → 200 ──────────────────────────────────────


def test_register_events_retries_5xx_then_succeeds(monkeypatch):
    """Первый POST вернул 503 → ретрай → 200 OK. После успеха новых POST'ов
    не плодим. WARNING-log не пишется (мы успешно зарегистрировали).
    """
    from src.core import config as config_mod

    monkeypatch.setenv("LOGGING_SERVICE_URL", "http://logging.test.local")
    monkeypatch.setenv("LOGGING_SERVICE_API_KEY", "secret")
    config_mod.get_settings.cache_clear()

    # Чтобы retry не съел секунду — обнуляем delay в тестовом прогоне.
    monkeypatch.setattr(audit_events, "_REGISTER_EVENTS_RETRY_DELAY_SECONDS", 0.0)

    calls: list[dict] = []

    def _fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json})
        if len(calls) == 1:
            return httpx.Response(503, text="upstream busy")
        return httpx.Response(
            200,
            json={
                "total": len(json["events"]),
                "added": len(json["events"]),
                "updated": 0,
            },
        )

    monkeypatch.setattr(audit_events.httpx, "post", _fake_post)

    audit_events.register_events()

    # Ровно две попытки: первая 503, вторая 200. Третьего POST'а быть не должно.
    assert len(calls) == 2, calls
    # URL и payload-формат — те же между попытками (идемпотентный POST).
    assert calls[0]["url"].endswith("/api/logging/v1/services/auth_service/events")
    assert calls[0]["json"] == calls[1]["json"]


def test_register_events_skips_when_logging_url_unset(monkeypatch):
    """Без LOGGING_SERVICE_URL — функция выходит до httpx.post."""
    from src.core import config as config_mod

    monkeypatch.delenv("LOGGING_SERVICE_URL", raising=False)
    monkeypatch.delenv("LOGGING_SERVICE_API_KEY", raising=False)
    config_mod.get_settings.cache_clear()

    called = {"posted": False}

    def _fake_post(*a, **kw):
        called["posted"] = True
        return httpx.Response(200, json={"total": 0, "added": 0, "updated": 0})

    monkeypatch.setattr(audit_events.httpx, "post", _fake_post)

    audit_events.register_events()

    assert called["posted"] is False
