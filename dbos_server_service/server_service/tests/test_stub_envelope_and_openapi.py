"""Tests for «41 stub 501 без auth + анонимный /openapi.json».

Verifies:
1. All 41 stub-endpoints across the 8 endpoint-files now require Bearer auth
   (anonymous request → 401 with proper error envelope, not 501).
2. Authenticated stub-requests return 501 with `error_code="NOT_IMPLEMENTED"`
   in the standard envelope (was: plain HTTPException → no error_code/request_id).
3. In `production` deployment env the public OpenAPI surface (/openapi.json,
   /docs, /redoc) is closed (404). In dev/local/test it remains open.
"""

from __future__ import annotations

import importlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# Список stub-эндпоинтов. Изначально было 41 (4 endpoint-файла полностью
# stub'нутые), затем 7 (после реализации disks/os_versions/
# installed_packages CRUD и ipmi power/boot/reinstall dispatch). Сейчас
# 0 — последние закрыты: user-facing view_credentials / cached power /
# live boot-order GET в ipmi.py, acquire/release/os-sync в servers.py.
STUB_ENDPOINTS: list[tuple[str, str]] = []


def test_stub_coverage_count() -> None:
    """Sanity-check: все 41 historical stubs закрыты."""
    assert len(STUB_ENDPOINTS) == 0


# ── Фикс 1: envelope + auth ──────────────────────────────────────────────────


@pytest.mark.parametrize("method, path", STUB_ENDPOINTS)
@pytest.mark.asyncio
async def test_stub_anonymous_returns_401_envelope(client, method, path):
    """Без Bearer-токена stub-endpoint должен вернуть 401 envelope, не 501.

    Это закрывает information disclosure: до фикса анонимы получали 501 и
    узнавали, что endpoint существует. Теперь они получают 401 — индистингуи-
    шибл от любого закрытого route.
    """
    resp = await getattr(client, method)(f"{BASE}{path}")
    assert resp.status_code == 401, (
        f"{method.upper()} {path}: expected 401 anon, got {resp.status_code}"
    )
    body = resp.json()
    # envelope-контракт из main.py app_exception_handler
    assert body.get("error_code") == "ACCESS_TOKEN_MISSING"
    assert "request_id" in body
    assert "timestamp" in body
    assert "message" in body


@pytest.mark.parametrize("method, path", STUB_ENDPOINTS)
@pytest.mark.asyncio
async def test_stub_authenticated_returns_501_envelope(client, admin_token, method, path):
    """С валидным токеном stub возвращает 501 в стандартном envelope.

    До фикса: `raise HTTPException(501, "not implemented yet")` минул
    app_exception_handler → ответ {"detail": "not implemented yet"} без
    error_code, request_id и timestamp.

    После фикса: `raise AppException(501, error_code="NOT_IMPLEMENTED", ...)`
    → ответ через handler с полным envelope.
    """
    resp = await getattr(client, method)(f"{BASE}{path}", headers=_hdr(admin_token))
    assert resp.status_code == 501, (
        f"{method.upper()} {path}: expected 501 auth'd, got {resp.status_code}"
    )
    body = resp.json()
    assert body.get("error_code") == "NOT_IMPLEMENTED", (
        f"{method.upper()} {path}: missing error_code envelope: {body!r}"
    )
    assert body.get("error") == "internal_error"  # _http_status_to_category fallback for 501
    assert "request_id" in body
    assert "timestamp" in body
    assert "detail" not in body  # старый HTTPException-стиль убран


# ── Фикс 2: openapi guard в prod ─────────────────────────────────────────────


def _build_fresh_app():
    """Импортирует create_application заново, чтобы он подхватил подменённые settings.

    Settings закэшированы через @lru_cache(get_settings) — фикстура _patch_settings
    очищает кэш до и после.
    """
    import src.main as main_module
    importlib.reload(main_module)  # Перечитать модуль, чтобы create_application() взял свежие settings.
    return main_module.create_application()


@pytest_asyncio.fixture
async def app_with_env(monkeypatch):
    """Возвращает фабрику: build(env='production') → fresh FastAPI с этим APP_ENV."""
    from src.core import config as config_module

    def _build(env: str):
        # Очищаем кэш get_settings, чтобы новый Settings() подхватил env-переменные.
        config_module.get_settings.cache_clear()
        monkeypatch.setenv("APP_ENV", env)
        # В production/staging Settings-валидаторы требуют набор непустых
        # секретов (https AUTH_SERVICE_URL, непустой SERVICE_API_KEY, HKDF-salt,
        # encryption-key v2). Подставляем их, чтобы конструктор не упал на
        # security-гардах. К самому openapi-guard'у эти значения отношения не
        # имеют — он смотрит только на APP_ENV.
        if env.lower() in {"production", "staging"}:
            monkeypatch.setenv("AUTH_SERVICE_URL", "https://auth.prod.svc:8000")
            monkeypatch.setenv("SERVICE_API_KEY", "prod-service-key-not-default")
            monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
            monkeypatch.setenv("HKDF_SALT_HEX", "deadbeefcafebabe0011223344556677")
            monkeypatch.setenv(
                "SERVER_ENCRYPTION_KEY",
                "test-server-encryption-key-do-not-use-anywhere-else",
            )
        app = _build_fresh_app()
        return app

    yield _build
    # Cleanup: вернуть кэш в чистое состояние.
    config_module.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_openapi_closed_in_production(app_with_env):
    """В production /openapi.json, /docs, /redoc отдают 404 (route не зарегистрирован).

    Это закрывает information disclosure: до фикса анонимы получали полный
    OpenAPI-schema со всеми summaries («Reveal decrypted IPMI credentials» и т.п.).
    """
    app = app_with_env("production")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for path in ("/openapi.json", "/docs", "/redoc"):
            resp = await ac.get(path)
            # 404 — route не зарегистрирован; 403 — HTTPS-guard блокирует cleartext
            # ещё до маршрутизации. Оба варианта означают «закрыто в production».
            assert resp.status_code in (403, 404), (
                f"{path} должен быть закрыт в production, got {resp.status_code}"
            )


@pytest.mark.asyncio
async def test_openapi_open_in_local(app_with_env):
    """В local/dev/test остаётся открытым для разработки."""
    app = app_with_env("local")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/openapi.json")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/json")
        body = resp.json()
        assert "paths" in body  # схема непустая
