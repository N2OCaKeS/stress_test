"""Outbound lifecycle-callback'и в secret_service.

Покрытие:
  * `notify_user_deleted` / `notify_dept_deleted` / `notify_dept_service_access_revoked`
    шлют POST в `/api/secret/v1/internal/lifecycle/*` с правильным
    payload'ом и Bearer'ом;
  * Empty `SECRET_SERVICE_URL` → no-op c WARNING (dev/test без secret_service);
  * 5xx от secret_service → audit `secret_lifecycle.notify_failed`,
    наружу не пробрасывается;
  * Transport-error (ConnectError) → audit failed, не падает;
  * `revoke_service_access(service="secret_service")` дёргает notify;
    другой service — нет.

Тесты НЕ ходят в сеть: подменяем httpx через MockTransport / monkeypatch
на `secret_service_client._client`.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from src.services import audit_service, department_service, secret_service_client


@pytest.fixture(autouse=True)
def _reset_secret_client():
    """Гарантируем чистый slot между тестами + восстанавливаем после."""
    secret_service_client.reset_for_tests()
    yield
    secret_service_client.reset_for_tests()


@pytest.fixture
def _settings_with_secret(monkeypatch):
    """Подменить get_settings.cache на инстанс с непустым SECRET_SERVICE_URL.

    `Settings()` читает env, поэтому проще монокипатчить getattr-доступ:
    клиент читает settings.secret_service_url / .secret_internal_api_key /
    .secret_service_tls_verify, остальное не трогаем.
    """
    from src.core.config import get_settings

    real = get_settings()

    class _Stub:
        def __getattr__(self, name):
            if name == "secret_service_url":
                return "http://secret-mock"
            if name == "secret_internal_api_key":
                return "test-secret-key"
            if name == "secret_service_tls_verify":
                return True
            return getattr(real, name)

    stub = _Stub()
    monkeypatch.setattr(
        "src.services.secret_service_client.get_settings", lambda: stub,
    )
    return stub


def _mock_pool(monkeypatch, handler) -> httpx.AsyncClient:
    """Подменить module-level `_client` на pooled mock-transport."""
    pooled = httpx.AsyncClient(
        base_url="http://secret-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(secret_service_client, "_client", pooled)
    return pooled


# ── notify_user_deleted ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_user_deleted_posts_to_correct_endpoint(
    monkeypatch, _settings_with_secret,
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen.append({
            "url": str(request.url),
            "auth": request.headers.get("Authorization"),
            "body": json.loads(request.content.decode()),
        })
        return httpx.Response(200, json={"blocked_count": 0, "deleted_count": 0})

    pooled = _mock_pool(monkeypatch, handler)
    try:
        await secret_service_client.notify_user_deleted(
            user_id="usr_42", actor_id="usr_admin", actor_username="admin",
        )
    finally:
        await pooled.aclose()

    assert len(seen) == 1
    assert seen[0]["url"].endswith("/api/secret/v1/internal/lifecycle/user-deleted")
    assert seen[0]["auth"] == "Bearer test-secret-key"
    assert seen[0]["body"] == {
        "user_id": "usr_42",
        "actor_id": "usr_admin",
        "actor_username": "admin",
    }


# ── notify_dept_deleted ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_dept_deleted_posts_to_correct_endpoint(
    monkeypatch, _settings_with_secret,
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen.append({
            "url": str(request.url),
            "body": json.loads(request.content.decode()),
        })
        return httpx.Response(200, json={
            "blocked_count": 0, "dept_grants_revoked": 0,
            "role_acls_revoked": 0, "errors": [],
        })

    pooled = _mock_pool(monkeypatch, handler)
    try:
        await secret_service_client.notify_dept_deleted(
            dept_id="dep_7", actor_id="usr_admin", actor_username="admin",
        )
    finally:
        await pooled.aclose()

    assert len(seen) == 1
    assert seen[0]["url"].endswith("/api/secret/v1/internal/lifecycle/dept-deleted")
    assert seen[0]["body"] == {
        "dept_id": "dep_7",
        "actor_id": "usr_admin",
        "actor_username": "admin",
    }


# ── notify_dept_service_access_revoked ─────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_dept_service_access_revoked_posts_payload(
    monkeypatch, _settings_with_secret,
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen.append({
            "url": str(request.url),
            "body": json.loads(request.content.decode()),
        })
        return httpx.Response(200, json={
            "dept_grants_revoked": 0, "role_acls_revoked": 0, "errors": [],
        })

    pooled = _mock_pool(monkeypatch, handler)
    try:
        await secret_service_client.notify_dept_service_access_revoked(
            dept_id="dep_9",
            service="secret_service",
            actor_id="usr_admin",
            actor_username="admin",
        )
    finally:
        await pooled.aclose()

    assert len(seen) == 1
    assert seen[0]["url"].endswith(
        "/api/secret/v1/internal/lifecycle/dept-service-access-revoked",
    )
    assert seen[0]["body"] == {
        "dept_id": "dep_9",
        "service": "secret_service",
        "actor_id": "usr_admin",
        "actor_username": "admin",
    }


# ── Empty SECRET_SERVICE_URL → no-op with WARNING ──────────────────────────


@pytest.mark.asyncio
async def test_empty_url_skips_emit_with_warning(monkeypatch, caplog):
    """Пустой URL = dev/test без secret_service. WARNING в лог, без POST'ов."""
    from src.core.config import get_settings

    real = get_settings()

    class _Stub:
        def __getattr__(self, name):
            if name == "secret_service_url":
                return ""
            if name == "secret_internal_api_key":
                return ""
            if name == "secret_service_tls_verify":
                return True
            return getattr(real, name)

    monkeypatch.setattr(
        "src.services.secret_service_client.get_settings", lambda: _Stub(),
    )

    # Любой live-client должен быть None — иначе тест не валиден.
    assert secret_service_client._client is None

    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        posted.append(str(request.url))
        return httpx.Response(200, json={})

    # Подсадить mock-pool — он НЕ должен дёрнуться, потому что url пустой
    # и код уходит в early-return до self._client.
    pooled = httpx.AsyncClient(
        base_url="http://should-not-be-called",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(secret_service_client, "_client", pooled)

    try:
        with caplog.at_level(logging.WARNING, logger="src.services.secret_service_client"):
            await secret_service_client.notify_user_deleted(
                "usr_x", "usr_admin", "admin",
            )
    finally:
        await pooled.aclose()

    assert posted == []
    assert any("SECRET_SERVICE_URL is empty" in rec.message for rec in caplog.records)


# ── 5xx от secret_service → audit failed, не raise ─────────────────────────


@pytest.mark.asyncio
async def test_http_500_emits_audit_failed_no_raise(
    monkeypatch, _settings_with_secret,
):
    """500 от secret_service → audit `secret_lifecycle.notify_failed`, нет crash'а."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error_code": "LIFECYCLE_HANDLER_FAILED"})

    pooled = _mock_pool(monkeypatch, handler)

    audit_calls: list[dict] = []

    def fake_emit(action, **kwargs):
        audit_calls.append({"action": action, **kwargs})

    monkeypatch.setattr(audit_service, "emit", fake_emit)

    try:
        # Должно вернуться без exception'а.
        await secret_service_client.notify_user_deleted(
            "usr_a", "usr_admin", "admin",
        )
    finally:
        await pooled.aclose()

    assert any(
        c["action"] == "secret_lifecycle.notify_failed"
        and c["details"]["reason"] == "http_error"
        and c["details"]["status_code"] == 500
        and c["details"]["endpoint"] == "user-deleted"
        for c in audit_calls
    )


@pytest.mark.asyncio
async def test_transport_error_emits_audit_failed_no_raise(
    monkeypatch, _settings_with_secret,
):
    """ConnectError → audit failed + warning, не падаем."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns lookup failed")

    pooled = _mock_pool(monkeypatch, handler)

    audit_calls: list[dict] = []

    def fake_emit(action, **kwargs):
        audit_calls.append({"action": action, **kwargs})

    monkeypatch.setattr(audit_service, "emit", fake_emit)

    try:
        await secret_service_client.notify_dept_deleted(
            "dep_a", "usr_admin", "admin",
        )
    finally:
        await pooled.aclose()

    assert any(
        c["action"] == "secret_lifecycle.notify_failed"
        and c["details"]["reason"] == "transport_error"
        and c["details"]["endpoint"] == "dept-deleted"
        for c in audit_calls
    )


# ── Wiring: department_service.revoke_service_access → notify ──────────────


@pytest.mark.asyncio
async def test_revoke_service_access_for_secret_service_triggers_notify(
    db, account_admin, dept_a, monkeypatch,
):
    """Когда у отдела отзывают access к "secret_service" — notify дёрнут."""
    from src.services.department_service import grant_service_access
    from src.models import PlatformService

    # Регистрируем secret_service как платформенный сервис + грант отделу.
    svc = PlatformService(
        service_name="secret_service",
        display_name="Secret Service",
        is_active=True,
    )
    db.add(svc)
    await db.flush()
    await grant_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_a.id, service_name="secret_service",
    )

    captured: list[dict] = []

    async def fake_notify(dept_id, service, actor_id, actor_username):
        captured.append({
            "dept_id": dept_id, "service": service,
            "actor_id": actor_id, "actor_username": actor_username,
        })

    monkeypatch.setattr(
        "src.services.department_service.secret_service_client."
        "notify_dept_service_access_revoked",
        fake_notify,
    )

    await department_service.revoke_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_a.id, service_name="secret_service",
    )

    assert len(captured) == 1
    assert captured[0]["dept_id"] == dept_a.id
    assert captured[0]["service"] == "secret_service"
    assert captured[0]["actor_id"] == account_admin.id
    assert captured[0]["actor_username"] == account_admin.username


@pytest.mark.asyncio
async def test_revoke_service_access_for_other_service_does_not_notify(
    db, account_admin, dept_a_with_service, service_x, monkeypatch,
):
    """Revoke access к другому сервису (service_x) — notify НЕ дёргается."""

    captured: list[dict] = []

    async def fake_notify(dept_id, service, actor_id, actor_username):
        captured.append({"service": service})

    monkeypatch.setattr(
        "src.services.department_service.secret_service_client."
        "notify_dept_service_access_revoked",
        fake_notify,
    )

    await department_service.revoke_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_a_with_service.id, service_name=service_x.service_name,
    )

    assert captured == []


@pytest.mark.asyncio
async def test_revoke_service_access_swallows_notify_exception(
    db, account_admin, dept_a, monkeypatch,
):
    """Любой raise внутри клиента не должен ронять revoke_service_access."""
    from src.services.department_service import grant_service_access
    from src.models import PlatformService

    svc = PlatformService(
        service_name="secret_service",
        display_name="Secret Service",
        is_active=True,
    )
    db.add(svc)
    await db.flush()
    await grant_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_a.id, service_name="secret_service",
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("client exploded")

    monkeypatch.setattr(
        "src.services.department_service.secret_service_client."
        "notify_dept_service_access_revoked",
        boom,
    )

    # Должно отработать без exception'а — revoke завершается, notify дропается.
    await department_service.revoke_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_a.id, service_name="secret_service",
    )
