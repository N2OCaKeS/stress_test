"""Security hardening — фиксы по `bot_service` denied audit, `AppException`
redact, login timing-oracle, PAT scope/expiry validation, CORS/security
headers, OAuthTokenRequest, dept-transfer role purge, SERVICE_API_KEYS
dual-mode.

Реальная PG-сессия + httpx ASGI-клиент; audit-emit перехватывается через
monkeypatch (captured-fixture)."""

import os
import time

import pytest
import pytest_asyncio

from src.core.exceptions import AppException, AuthorizationError
from tests.conftest import (
    _assign_role,
    _grant_service,
    _make_dept,
    _make_role_def,
    _make_service,
    _make_user,
)


# ── Audit capture (in-process) ────────────────────────────────────────────────


@pytest.fixture()
def captured_audit(monkeypatch):
    """Перехват emit'ов через monkeypatch."""
    captured: list[dict] = []

    original_emit = None

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    # Также подменим импорт в модулях, которые делают `from src.services import audit_service`
    # — они держат ссылку на модуль, а наш monkeypatch меняет attribute.
    return captured


# ── Bot denied cross-tenant ───────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def two_depts_bot_setup(db):
    """dept_a с бот'ом, dept_b с admin'ом — попытки cross-tenant."""
    a = await _make_dept(db, "p2a_dept_a")
    b = await _make_dept(db, "p2a_dept_b")
    svc = await _make_service(db, "p2a_svc")
    await _grant_service(db, a.id, svc.service_name)
    await _grant_service(db, b.id, svc.service_name)
    dept_admin_b = await _make_user(
        db, "p2a_admin_b", "P@ssw0rd123!",
        department_id=b.id, platform_role="department_admin",
    )

    from src.models import BotAccount
    from src.utils.ids import _new_id
    bot = BotAccount(
        id=_new_id("bot_"), name="p2a_bot",
        department_id=a.id, allowed_services=[svc.service_name],
        is_active=True, status="active", created_by=None,
    )
    db.add(bot)
    await db.flush()
    await db.commit()
    return {"dept_a": a, "dept_b": b, "service": svc, "bot": bot, "admin_b": dept_admin_b}


async def test_bot_token_create_cross_tenant_emits_failure(
    db, two_depts_bot_setup, captured_audit,
):
    """department_admin отдела B пытается выдать токен боту dept A → failure audit + raise."""
    from src.services import bot_service

    bot = two_depts_bot_setup["bot"]
    admin_b = two_depts_bot_setup["admin_b"]

    with pytest.raises(AuthorizationError) as ei:
        await bot_service.create_bot_token(
            db=db, actor_id=admin_b.id,
            actor_role="department_admin", bot_id=bot.id, name="t",
        )
    assert ei.value.error_code == "BOT_ROLE_MGMT_FORBIDDEN"

    failed = [e for e in captured_audit if e.get("status") == "failure"]
    assert any(
        e["action"] == "bot.token_create"
        and e["details"]["reason"] == "cross_tenant_bot"
        and e["details"]["bot_id"] == bot.id
        for e in failed
    ), f"no failure event for bot.token_create: {failed}"


async def test_bot_roles_assign_cross_tenant_emits_failure(
    db, two_depts_bot_setup, captured_audit,
):
    from src.services import bot_service

    bot = two_depts_bot_setup["bot"]
    admin_b = two_depts_bot_setup["admin_b"]
    svc = two_depts_bot_setup["service"]

    with pytest.raises(AuthorizationError):
        await bot_service.assign_bot_roles(
            db=db, actor_id=admin_b.id, actor_role="department_admin",
            bot_id=bot.id, service_name=svc.service_name, roles=["reader"],
        )
    failed = [
        e for e in captured_audit
        if e.get("status") == "failure" and e["action"] == "bot.roles_assign"
    ]
    assert failed, f"no failure event for bot.roles_assign: {captured_audit}"
    assert failed[0]["details"]["reason"] == "cross_tenant_bot"


async def test_bot_token_revoke_cross_tenant_emits_failure(
    db, two_depts_bot_setup, captured_audit,
):
    from src.services import bot_service

    bot = two_depts_bot_setup["bot"]
    admin_b = two_depts_bot_setup["admin_b"]

    with pytest.raises(AuthorizationError):
        await bot_service.revoke_bot_token(
            db=db, actor_id=admin_b.id, actor_role="department_admin",
            bot_id=bot.id, token_id="bot_token_x",
        )
    failed_actions = [
        e["action"] for e in captured_audit if e.get("status") == "failure"
    ]
    assert "bot.token_revoke" in failed_actions


# ── AppException details redact ───────────────────────────────────────────────


async def test_app_exception_details_password_redacted(client):
    """AppException с password в details — клиент видит <PASSWORD>, не plaintext."""
    # Воспроизведём через test endpoint: вызовем что-то, что генерит AppException
    # с подозрительными ключами. Простейший путь — handler уже redact'ит details,
    # проверяем напрямую через JSONResponse.
    from src.main import create_application
    from src.core.exceptions import AppException
    from fastapi import FastAPI

    app: FastAPI = create_application()

    @app.get("/__test_leak__")
    async def _leak():
        raise AppException(
            error_code="TEST_LEAK",
            message="leak",
            details={"password": "p@ss-secret", "ok": "fine"},
            http_status=400,
        )

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/__test_leak__")
    body = resp.json()
    assert body["details"]["password"] == "<PASSWORD>"
    assert body["details"]["ok"] == "fine"


async def test_app_exception_details_token_redacted(client):
    """Также проверяем dbos_pat_-токен в details — заменён на <TOKEN>."""
    from src.main import create_application
    from src.core.exceptions import AppException

    app = create_application()

    @app.get("/__test_leak_token__")
    async def _leak():
        raise AppException(
            error_code="TEST_LEAK",
            message="leak",
            details={"token": "dbos_pat_abcdefgh12345678"},
            http_status=400,
        )

    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/__test_leak_token__")
    body = resp.json()
    assert body["details"]["token"] == "<TOKEN>"


# ── Login timing oracle ───────────────────────────────────────────────────────


async def test_login_unknown_user_runs_argon2_verify(client):
    """Для несуществующего юзера timing должен быть сопоставим с Argon2 verify.

    Мы не меряем абсолютные ms (нестабильно в CI), но проверяем, что вызов
    `verify_password` происходит — patch'аем модуль и считаем вызовы.
    """
    import src.services.auth_service as auth_mod

    call_count = {"n": 0}
    real_verify = auth_mod.verify_password

    def counting_verify(plain, hashed):
        call_count["n"] += 1
        return real_verify(plain, hashed)

    auth_mod.verify_password = counting_verify
    try:
        resp = await client.post(
            "/api/auth/v1/login",
            json={"username": "i_do_not_exist", "password": "x"},
        )
    finally:
        auth_mod.verify_password = real_verify

    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"
    assert call_count["n"] >= 1, "verify_password не был вызван для unknown user"


async def test_login_unknown_user_timing_close_to_known_user(client, account_admin):
    """Smoke-проверка: timing unknown ≈ timing wrong-password (в пределах ratio).

    На Argon2id ~100ms unknown=0ms был бы oracle (ratio ~10x). Сейчас оба
    делают verify_password — допускаем ratio до 2x для CI-флуктуаций.
    """
    # Warm-up
    await client.post(
        "/api/auth/v1/login",
        json={"username": "t_admin", "password": "Admin1234!"},
    )

    start = time.perf_counter()
    await client.post(
        "/api/auth/v1/login",
        json={"username": "t_admin", "password": "wrong"},
    )
    known_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    await client.post(
        "/api/auth/v1/login",
        json={"username": "does_not_exist_xyz", "password": "x"},
    )
    unknown_ms = (time.perf_counter() - start) * 1000

    # Ratio: unknown должен быть НЕ кардинально меньше known.
    # Допустим, что unknown >= 30% от known (Argon2 уже работает).
    assert unknown_ms >= known_ms * 0.3, (
        f"unknown ({unknown_ms:.0f}ms) сильно быстрее known ({known_ms:.0f}ms) — timing oracle"
    )


# ── PAT scope + expires_at validation ─────────────────────────────────────────


async def test_pat_create_scope_outside_dept_returns_422(client, user_a_token, dept_a_with_service, service_x):
    """user_a в dept_a (с access только к service_x) → PAT с scope `other_service` → 422."""
    resp = await client.post(
        "/api/auth/v1/tokens",
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "bad_scope", "allowed_services": ["other_service_no_access"]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"
    assert "other_service_no_access" in body["details"]["forbidden_services"]


async def test_pat_create_expires_in_past_returns_422(client, user_a_token):
    """expires_at < now → 422 INVALID_TOKEN_EXPIRY."""
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    resp = await client.post(
        "/api/auth/v1/tokens",
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "expired_pat", "expires_at": past, "allowed_services": ["service_x"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_TOKEN_EXPIRY"


async def test_pat_create_valid_scope_and_future_expiry_ok(
    client, user_a_token, dept_a_with_service, service_x,
):
    """Happy path: scope в пределах dept + expires_at в будущем → 201."""
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    resp = await client.post(
        "/api/auth/v1/tokens",
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={
            "name": "good_pat",
            "expires_at": future,
            "allowed_services": [service_x.service_name],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["token"].startswith("dbos_pat_")


# ── Security headers + CORS + introspect rate-limit ───────────────────────────


async def test_security_headers_present(client):
    """X-Frame-Options, X-Content-Type-Options, Referrer-Policy, CSP — на каждом ответе."""
    resp = await client.get("/api/auth/v1/health")
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp


async def test_hsts_header_off_by_default(client):
    """HSTS не должен включаться без явного `SECURITY_HSTS_ENABLED=true`."""
    resp = await client.get("/api/auth/v1/health")
    assert "Strict-Transport-Security" not in resp.headers


async def test_introspect_rate_limit_enforced(monkeypatch, account_admin):
    """introspect под per-IP rate-limit, как и /login. С тайтом 3/minute 4-й → 429."""
    monkeypatch.setenv("INTROSPECT_RATE_LIMIT", "3/minute")
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()
    try:
        from src.main import create_application
        from src.dependencies.db import get_db

        app = create_application()
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy import event

        # Лёгкий стенд — отдельная сессия, без shared `db` fixture
        engine = create_async_engine(
            os.environ.get(
                "TEST_DATABASE_URL",
                "postgresql+psycopg://app_user:app_password@postgres:5432/test_auth",
            ),
            pool_pre_ping=True,
        )
        conn = await engine.connect()
        await conn.begin()
        await conn.begin_nested()
        session = AsyncSession(bind=conn, expire_on_commit=False)

        @event.listens_for(session.sync_session, "after_transaction_end")
        def _restart(s, t):
            if t.nested and not t._parent.nested:
                s.begin_nested()

        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override

        headers = {"Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for i in range(3):
                r = await c.post(
                    "/api/auth/v1/authorization/introspect",
                    json={"token": "junk"}, headers=headers,
                )
                assert r.status_code != 429, f"#{i+1} → {r.status_code}"
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "junk"}, headers=headers,
            )
            assert r.status_code == 429

        await session.close()
        await conn.rollback()
        await conn.close()
        await engine.dispose()
    finally:
        config_mod.get_settings.cache_clear()


# ── OAuthTokenRequest grant_type обязателен + Literal ─────────────────────────


async def test_oauth_token_missing_grant_type_returns_422(client):
    """Без grant_type Pydantic v2 → 422."""
    resp = await client.post(
        "/api/auth/v1/oauth2/token",
        data={"client_id": "x", "client_secret": "y"},
    )
    assert resp.status_code == 422


async def test_oauth_token_bad_grant_type_returns_422(client):
    """grant_type='password' не в whitelist → 422 (а не fallback UNSUPPORTED_GRANT_TYPE)."""
    resp = await client.post(
        "/api/auth/v1/oauth2/token",
        json={"grant_type": "password", "client_id": "x", "client_secret": "y"},
    )
    assert resp.status_code == 422


# ── Cleanup UserServiceRole при смене department_id ───────────────────────────


async def test_dept_transfer_deactivates_old_service_roles(db):
    """Service-уровень: update_user со сменой department_id деактивирует UserServiceRole.

    Прямой вызов сервис-функции на той же сессии — обходит мульти-коннект
    SAVEPOINT-проблему с client.patch().
    """
    from src.models import UserServiceRole as USR
    from sqlalchemy import select
    from src.services import user_service

    dept_a = await _make_dept(db, "p2g_dept_a")
    dept_b = await _make_dept(db, "p2g_dept_b")
    svc = await _make_service(db, "p2g_svc")
    await _grant_service(db, dept_a.id, svc.service_name)
    await _grant_service(db, dept_b.id, svc.service_name)
    admin = await _make_user(
        db, "p2g_admin", "Admin1234!", platform_role="account_admin",
    )
    user = await _make_user(db, "p2g_user", "User1234!", department_id=dept_a.id)
    await _assign_role(db, user.id, svc.service_name, "operator")
    await db.commit()

    rows = list(await db.scalars(
        select(USR).where(USR.user_id == user.id, USR.is_active.is_(True))
    ))
    assert len(rows) == 1

    await user_service.update_user(
        db=db, actor_id=admin.id, actor_role="account_admin",
        user_id=user.id, updates={"department_id": dept_b.id},
    )

    after = list(await db.scalars(
        select(USR).where(USR.user_id == user.id, USR.is_active.is_(True))
    ))
    assert after == [], "roles must be deactivated on dept transfer"


async def test_dept_transfer_emits_roles_purged_audit(
    client, admin_token, db, captured_audit,
):
    dept_a = await _make_dept(db, "p2g_audit_a")
    dept_b = await _make_dept(db, "p2g_audit_b")
    svc = await _make_service(db, "p2g_audit_svc")
    await _grant_service(db, dept_a.id, svc.service_name)
    await _grant_service(db, dept_b.id, svc.service_name)
    user = await _make_user(db, "p2g_audit_user", "User1234!", department_id=dept_a.id)
    await _assign_role(db, user.id, svc.service_name, "operator")
    await db.commit()

    captured_audit.clear()

    resp = await client.patch(
        f"/api/auth/v1/users/{user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"department_id": dept_b.id},
    )
    assert resp.status_code == 200

    purged_events = [
        e for e in captured_audit if e["action"] == "user.roles_purged_on_transfer"
    ]
    assert purged_events, f"no purged audit emitted; got: {[e['action'] for e in captured_audit]}"
    details = purged_events[0]["details"]
    assert details["from_department_id"] == dept_a.id
    assert details["to_department_id"] == dept_b.id
    assert details["roles_purged_count"] >= 1


async def test_dept_unchanged_does_not_purge_roles(
    client, admin_token, db, captured_audit,
):
    """PATCH без смены dept_id — UserServiceRole не трогаем."""
    dept_a = await _make_dept(db, "p2g_same_a")
    svc = await _make_service(db, "p2g_same_svc")
    await _grant_service(db, dept_a.id, svc.service_name)
    user = await _make_user(db, "p2g_same_user", "User1234!", department_id=dept_a.id)
    await _assign_role(db, user.id, svc.service_name, "operator")
    await db.commit()

    captured_audit.clear()

    resp = await client.patch(
        f"/api/auth/v1/users/{user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": "x@example.com"},
    )
    assert resp.status_code == 200

    purged = [
        e for e in captured_audit if e["action"] == "user.roles_purged_on_transfer"
    ]
    assert purged == []


# ── SERVICE_API_KEYS dual-mode ────────────────────────────────────────────────


async def test_service_api_keys_dual_mode_happy_path(monkeypatch, db):
    """С SERVICE_API_KEYS={loging: key_l, server: key_s} — header выбирает ключ."""
    monkeypatch.setenv(
        "SERVICE_API_KEYS",
        '{"loging_service": "key_loging", "server_service": "key_server"}',
    )
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()
    try:
        from src.main import create_application
        from src.dependencies.db import get_db

        app = create_application()

        async def _override():
            yield db

        app.dependency_overrides[get_db] = _override

        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # loging_service со своим ключом — ok (или 4xx по body, но не 401)
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "x"},
                headers={
                    "Authorization": "Bearer key_loging",
                    "X-Service-Identity": "loging_service",
                },
            )
            assert r.status_code != 401, f"unexpected 401: {r.text}"

            # loging_service с server_service-ключом → 401
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "x"},
                headers={
                    "Authorization": "Bearer key_server",
                    "X-Service-Identity": "loging_service",
                },
            )
            assert r.status_code == 401

            # Нет header'а — обязателен в dual-mode → 401
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "x"},
                headers={"Authorization": "Bearer key_loging"},
            )
            assert r.status_code == 401
            assert r.json()["error_code"] == "MISSING_SERVICE_IDENTITY"
    finally:
        config_mod.get_settings.cache_clear()


async def test_service_api_keys_unknown_identity_rejected(monkeypatch, db):
    """Identity не в SERVICE_API_KEYS → 401."""
    monkeypatch.setenv(
        "SERVICE_API_KEYS",
        '{"loging_service": "key_l"}',
    )
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()
    try:
        from src.main import create_application
        from src.dependencies.db import get_db

        app = create_application()

        async def _override():
            yield db

        app.dependency_overrides[get_db] = _override

        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "x"},
                headers={
                    "Authorization": "Bearer key_l",
                    "X-Service-Identity": "unknown_service",
                },
            )
            assert r.status_code == 401
    finally:
        config_mod.get_settings.cache_clear()


async def test_service_api_keys_empty_falls_back_to_legacy(monkeypatch, db):
    """Если SERVICE_API_KEYS пустой — старый SERVICE_API_KEY проходит без header'а."""
    monkeypatch.setenv("SERVICE_API_KEYS", "{}")
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()
    try:
        from src.main import create_application
        from src.dependencies.db import get_db

        app = create_application()

        async def _override():
            yield db

        app.dependency_overrides[get_db] = _override

        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "x"},
                headers={
                    "Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}",
                },
            )
            # 401 от introspect означает токен не валид, не header.
            # Мы хотим убедиться, что header SERVICE_API_KEY ОК — endpoint
            # за гвардом отрабатывает.
            body = r.json()
            assert body.get("error_code") != "INVALID_SERVICE_TOKEN"
            assert body.get("error_code") != "MISSING_SERVICE_IDENTITY"
    finally:
        config_mod.get_settings.cache_clear()


async def test_strict_service_api_keys_rejects_legacy_when_dict_set(monkeypatch, db):
    """STRICT_SERVICE_API_KEYS=true + непустой SERVICE_API_KEYS:
    legacy SERVICE_API_KEY больше не работает даже как корректный токен.
    """
    monkeypatch.setenv("SERVICE_API_KEYS", '{"loging_service": "key_l"}')
    monkeypatch.setenv("STRICT_SERVICE_API_KEYS", "true")
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()
    try:
        from src.main import create_application
        from src.dependencies.db import get_db

        app = create_application()

        async def _override():
            yield db

        app.dependency_overrides[get_db] = _override

        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Корректный per-service ключ + identity → OK (не 401 от гварда).
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "x"},
                headers={
                    "Authorization": "Bearer key_l",
                    "X-Service-Identity": "loging_service",
                },
            )
            assert r.json().get("error_code") not in {
                "INVALID_SERVICE_TOKEN",
                "MISSING_SERVICE_IDENTITY",
            }, f"per-service path должен пройти: {r.text}"

            # Legacy SERVICE_API_KEY без header'а: в strict-режиме reject.
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "x"},
                headers={
                    "Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}",
                },
            )
            assert r.status_code == 401
            # Сюда попадаем по верхней ветке (есть SERVICE_API_KEYS),
            # отсутствие header'а — MISSING_SERVICE_IDENTITY.
            assert r.json()["error_code"] == "MISSING_SERVICE_IDENTITY"

            # Legacy SERVICE_API_KEY с header'ом loging_service — ключ не
            # совпадает с key_l → INVALID_SERVICE_TOKEN.
            r = await c.post(
                "/api/auth/v1/authorization/introspect",
                json={"token": "x"},
                headers={
                    "Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}",
                    "X-Service-Identity": "loging_service",
                },
            )
            assert r.status_code == 401
            assert r.json()["error_code"] == "INVALID_SERVICE_TOKEN"
    finally:
        config_mod.get_settings.cache_clear()
