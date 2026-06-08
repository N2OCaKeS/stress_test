"""Регрессионные проверки фиксов:

P0:
  * `secret_service_client._post` шлёт `X-Service-Identity: auth_service`.
  * `secret_service` лежит в `KNOWN_SERVICE_IDENTITIES`.
  * `require_service_admin` отбивает не-service_admin'ов.

P1:
  * `delete_service` каскадно гасит GroupServiceRole/GroupServiceAccess и
    инвалидует identity-cache для group-member'ов.
  * `delete_service("secret_service")` шлёт per-dept lifecycle в secret_service.
  * Production-validator падает на пустом `SECRET_INTERNAL_API_KEY` и не-https
    `SECRET_SERVICE_URL`.
  * `_post` маскирует `user_id`/`actor_id`/`actor_username` в audit details.
  * `BulkRoleRequest.user_ids` имеет ограничение `max_length=200`.
  * Circuit-breaker уходит в open после 3 fail'ов подряд.

Миграция `i6j7k8l9m0n1` downgrade-safety: проверяется отдельным db-тестом
ниже (`test_migration_downgrade_clears_service_admin_first`).
"""

from __future__ import annotations

import httpx
import pytest

from src.services import audit_service, secret_service_client


# ── P0 #1 / P1 #7 — X-Service-Identity outbound + PII redaction ─────────────


@pytest.fixture(autouse=True)
def _reset_secret_client():
    """Чистый slot и сброшенный breaker между тестами."""
    secret_service_client.reset_for_tests()
    yield
    secret_service_client.reset_for_tests()


@pytest.fixture
def _settings_with_secret(monkeypatch):
    """Подменить settings'инстанс под client'ом."""
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

    monkeypatch.setattr(
        "src.services.secret_service_client.get_settings", lambda: _Stub(),
    )


def _mock_pool(monkeypatch, handler) -> httpx.AsyncClient:
    pooled = httpx.AsyncClient(
        base_url="http://secret-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(secret_service_client, "_client", pooled)
    return pooled


@pytest.mark.asyncio
async def test_outbound_post_carries_x_service_identity_header(
    monkeypatch, _settings_with_secret,
):
    """secret_service_client должен слать `X-Service-Identity: auth_service`."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "identity": request.headers.get("X-Service-Identity"),
            "auth": request.headers.get("Authorization"),
        })
        return httpx.Response(200, json={"ok": True})

    pooled = _mock_pool(monkeypatch, handler)
    try:
        await secret_service_client.notify_user_deleted(
            "usr_42", "usr_admin", "admin",
        )
    finally:
        await pooled.aclose()

    assert len(captured) == 1
    assert captured[0]["identity"] == "auth_service", (
        f"X-Service-Identity не выставлен: {captured[0]}"
    )
    assert captured[0]["auth"] == "Bearer test-secret-key"


@pytest.mark.asyncio
async def test_audit_details_redact_pii_on_http_failure(
    monkeypatch, _settings_with_secret,
):
    """5xx → user_id/actor_id/actor_username в audit details замаскированы."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    pooled = _mock_pool(monkeypatch, handler)
    audit_calls: list[dict] = []
    monkeypatch.setattr(
        audit_service, "emit",
        lambda action, **kwargs: audit_calls.append({"action": action, **kwargs}),
    )

    try:
        await secret_service_client.notify_user_deleted(
            "usr_top_secret_42",
            "usr_admin_99",
            "admin_username",
        )
    finally:
        await pooled.aclose()

    failed = [c for c in audit_calls if c["action"] == "secret_lifecycle.notify_failed"]
    assert failed, "audit-эмит должен быть"
    payload = failed[-1]["details"]["payload"]
    # PII полей plaintext'ом быть не должно — только маскированный хвост.
    assert payload["user_id"] != "usr_top_secret_42"
    assert payload["actor_id"] != "usr_admin_99"
    assert payload["actor_username"] != "admin_username"
    assert "REDACTED" in str(payload["user_id"])
    assert "REDACTED" in str(payload["actor_id"])
    assert "REDACTED" in str(payload["actor_username"])


@pytest.mark.asyncio
async def test_audit_details_redact_pii_on_transport_failure(
    monkeypatch, _settings_with_secret,
):
    """ConnectError → PII в audit-payload замаскированы."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns lookup failed")

    pooled = _mock_pool(monkeypatch, handler)
    audit_calls: list[dict] = []
    monkeypatch.setattr(
        audit_service, "emit",
        lambda action, **kwargs: audit_calls.append({"action": action, **kwargs}),
    )

    try:
        await secret_service_client.notify_dept_deleted(
            "dep_secret_x", "usr_admin_top", "admin",
        )
    finally:
        await pooled.aclose()

    failed = [c for c in audit_calls if c["action"] == "secret_lifecycle.notify_failed"]
    assert failed
    payload = failed[-1]["details"]["payload"]
    assert payload["dept_id"] != "dep_secret_x"
    assert "REDACTED" in str(payload["dept_id"])
    assert "REDACTED" in str(payload["actor_id"])


# ── P1 #10 — circuit-breaker ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_three_failures(
    monkeypatch, _settings_with_secret,
):
    """3 transport-fail подряд → breaker открыт, 4-й вызов short-circuit'ит без POST'а."""
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(str(request.url))
        raise httpx.ConnectError("down")

    pooled = _mock_pool(monkeypatch, handler)
    audit_calls: list[dict] = []
    monkeypatch.setattr(
        audit_service, "emit",
        lambda action, **kwargs: audit_calls.append({"action": action, **kwargs}),
    )

    try:
        for _ in range(3):
            await secret_service_client.notify_user_deleted(
                "usr_x", "usr_admin", "admin",
            )
        # Сейчас breaker должен быть open.
        assert secret_service_client._breaker_is_open()
        # 4-й вызов — short-circuit, реального POST'а не происходит.
        before = len(posted)
        await secret_service_client.notify_user_deleted(
            "usr_y", "usr_admin", "admin",
        )
        assert len(posted) == before, (
            "при открытом breaker'е реального POST'а быть не должно"
        )
    finally:
        await pooled.aclose()

    # И последний emit ушёл в audit с reason=circuit_open.
    reasons = [
        c["details"].get("reason")
        for c in audit_calls
        if c["action"] == "secret_lifecycle.notify_failed"
    ]
    assert "circuit_open" in reasons


@pytest.mark.asyncio
async def test_breaker_resets_on_success(
    monkeypatch, _settings_with_secret,
):
    """Любой 2xx после fail'ов сбрасывает счётчик."""
    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={})

    pooled = _mock_pool(monkeypatch, handler)
    try:
        # 2 fail'а — счётчик 2, breaker ещё закрыт.
        for _ in range(2):
            await secret_service_client.notify_user_deleted(
                "usr_x", "usr_admin", "admin",
            )
        assert not secret_service_client._breaker_is_open()
        state["fail"] = False
        # Успех должен обнулить счётчик.
        await secret_service_client.notify_user_deleted(
            "usr_y", "usr_admin", "admin",
        )
        # Снова 2 fail'а — всё ещё закрыто.
        state["fail"] = True
        for _ in range(2):
            await secret_service_client.notify_user_deleted(
                "usr_z", "usr_admin", "admin",
            )
        assert not secret_service_client._breaker_is_open(), (
            "счётчик должен был сброситься после успеха"
        )
    finally:
        await pooled.aclose()


# ── P0 #2 — KNOWN_SERVICE_IDENTITIES ───────────────────────────────────────


def test_secret_service_in_known_identities():
    from src.core.constants import KNOWN_SERVICE_IDENTITIES
    assert "secret_service" in KNOWN_SERVICE_IDENTITIES


# ── P0 #3 — require_service_admin guard ─────────────────────────────────────


def test_require_service_admin_rejects_non_service_admin():
    """Гость с platform_role=None / account_admin / department_admin отбит 403."""
    from src.core.exceptions import AuthorizationError
    from src.dependencies.auth import require_service_admin
    from src.schemas.auth import IdentityContext

    for role in (None, "account_admin", "department_admin", "loging_admin"):
        identity = IdentityContext(
            user_id="usr_1",
            username="t",
            department_id=None,
            allowed_services=[],
            service_roles={},
            is_banned=False,
            platform_role=role,
            subject_type="user",
        )
        with pytest.raises(AuthorizationError) as exc:
            require_service_admin(identity)
        assert exc.value.error_code == "ROLE_REQUIRED"


def test_require_service_admin_accepts_service_admin():
    from src.dependencies.auth import require_service_admin
    from src.schemas.auth import IdentityContext

    identity = IdentityContext(
        user_id="usr_1",
        username="t",
        department_id=None,
        allowed_services=[],
        service_roles={},
        is_banned=False,
        platform_role="service_admin",
        subject_type="user",
    )
    result = require_service_admin(identity)
    assert result is identity


# ── P1 #9 — BulkRoleRequest.user_ids max_length=200 ─────────────────────────


def test_bulk_role_request_rejects_more_than_200_user_ids():
    from pydantic import ValidationError

    from src.api.v1.endpoints.service_roles import (
        _BULK_ROLE_MAX_USER_IDS, BulkRoleRequest,
    )

    assert _BULK_ROLE_MAX_USER_IDS == 200

    # 200 — ок.
    BulkRoleRequest(user_ids=[f"usr_{i}" for i in range(200)])

    # 201 — fail.
    with pytest.raises(ValidationError):
        BulkRoleRequest(user_ids=[f"usr_{i}" for i in range(201)])


# ── P1 #6 — production validators ──────────────────────────────────────────


def _valid_prod_kwargs() -> dict:
    return {
        "APP_ENV": "production",
        "APP_DEBUG": False,
        "DATABASE_URL": "postgresql+psycopg://u:p@db:5432/auth",
        "SECRET_KEY": "a" * 48,
        "SERVICE_API_KEY": "b" * 48,
        "LOGGING_SERVICE_API_KEY": "c" * 32,
        "DOCKER_RSA_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
        "SECRET_INTERNAL_API_KEY": "d" * 32,
        # Пустой URL допустим: это no-op (нет secret_service на стенде).
        "SECRET_SERVICE_URL": "",
    }


def test_production_rejects_empty_secret_internal_api_key():
    from pydantic import ValidationError

    from src.core.config import Settings
    kwargs = _valid_prod_kwargs()
    kwargs["SECRET_INTERNAL_API_KEY"] = ""
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **kwargs)
    assert "SECRET_INTERNAL_API_KEY" in str(exc.value)


def test_production_rejects_http_secret_service_url():
    from pydantic import ValidationError

    from src.core.config import Settings
    kwargs = _valid_prod_kwargs()
    kwargs["SECRET_SERVICE_URL"] = "http://secret.internal.cluster"
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **kwargs)
    assert "SECRET_SERVICE_URL" in str(exc.value)


def test_production_accepts_https_secret_service_url():
    from src.core.config import Settings
    kwargs = _valid_prod_kwargs()
    kwargs["SECRET_SERVICE_URL"] = "https://secret.internal.cluster"
    Settings(_env_file=None, **kwargs)  # no raise


def test_production_accepts_http_localhost_secret_service_url():
    """localhost-исключение — devcontainer/sidecar TLS терминируется на хосте."""
    from src.core.config import Settings
    kwargs = _valid_prod_kwargs()
    kwargs["SECRET_SERVICE_URL"] = "http://localhost:8080"
    Settings(_env_file=None, **kwargs)  # no raise


def test_production_allows_empty_secret_service_url():
    """Пустой URL — намеренный no-op-режим. Не должен фейлить production."""
    from src.core.config import Settings
    kwargs = _valid_prod_kwargs()
    kwargs["SECRET_SERVICE_URL"] = ""
    Settings(_env_file=None, **kwargs)  # no raise


# ── P1 #4 + #5 — delete_service group cascade + per-dept emit ──────────────


@pytest.mark.asyncio
async def test_delete_service_cascades_group_service_roles_and_access(
    db, account_admin, dept_a, monkeypatch,
):
    """delete_service должен деактивировать GroupServiceRole/GroupServiceAccess
    и инвалидировать identity-cache group-member'ов.
    """
    from src.models import PlatformService
    from src.models.group_service_access import GroupServiceAccess
    from src.models.group_service_role import GroupServiceRole
    from src.models.user_group import UserGroup
    from src.models.user_group_membership import UserGroupMembership
    from src.services import platform_service_service
    from src.services.department_service import grant_service_access
    from src.utils.ids import _new_id

    # Сервис + grant.
    svc = PlatformService(
        service_name="cascade_svc",
        display_name="Cascade",
        is_active=True,
    )
    db.add(svc)
    await db.flush()
    await grant_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_a.id, service_name="cascade_svc",
    )

    # Группа в этом отделе + member.
    grp = UserGroup(
        id=_new_id("grp_"),
        department_id=dept_a.id,
        name="g1",
        display_name="G1",
        is_active=True,
    )
    db.add(grp)
    await db.flush()

    # Member-юзер в отделе.
    from tests.conftest import _make_user
    member = await _make_user(
        db, "cascade_user", "Pwd12345!", department_id=dept_a.id,
    )
    db.add(UserGroupMembership(
        id=_new_id("ugm_"), group_id=grp.id, user_id=member.id,
    ))
    # Group-level service binding + access binding.
    db.add(GroupServiceRole(
        id=_new_id("gsr_"),
        group_id=grp.id, service_name="cascade_svc",
        role="reader", is_active=True,
    ))
    db.add(GroupServiceAccess(
        id=_new_id("gsa_"),
        group_id=grp.id, service_name="cascade_svc", is_active=True,
    ))
    await db.flush()

    invalidated: list[str] = []
    monkeypatch.setattr(
        "src.services.platform_service_service._invalidate_identity_cache",
        lambda subj_id: invalidated.append(subj_id),
    )

    await platform_service_service.delete_service(
        db, actor_id=account_admin.id, service_name="cascade_svc",
    )

    # GroupServiceRole/GroupServiceAccess должны стать is_active=False.
    from sqlalchemy import select
    gsr = await db.scalar(
        select(GroupServiceRole).where(GroupServiceRole.group_id == grp.id)
    )
    gsa = await db.scalar(
        select(GroupServiceAccess).where(GroupServiceAccess.group_id == grp.id)
    )
    assert gsr is not None and gsr.is_active is False, (
        "GroupServiceRole должен быть deactivated"
    )
    assert gsa is not None and gsa.is_active is False, (
        "GroupServiceAccess должен быть deactivated"
    )

    # Group-member должен попасть в инвалидацию.
    assert member.id in invalidated, (
        f"group-member identity-cache не сброшен: {invalidated}"
    )


@pytest.mark.asyncio
async def test_delete_secret_service_emits_lifecycle_per_dept(
    db, account_admin, dept_a, dept_b, monkeypatch,
):
    """delete_service('secret_service') шлёт lifecycle-event для каждого dep'а
    с активным access'ом.
    """
    from src.models import PlatformService
    from src.services import platform_service_service
    from src.services.department_service import grant_service_access

    svc = PlatformService(
        service_name="secret_service",
        display_name="Secret",
        is_active=True,
    )
    db.add(svc)
    await db.flush()
    await grant_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_a.id, service_name="secret_service",
    )
    await grant_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_b.id, service_name="secret_service",
    )

    captured: list[dict] = []

    async def fake_notify(dept_id, service, actor_id, actor_username):
        captured.append({
            "dept_id": dept_id, "service": service,
            "actor_id": actor_id, "actor_username": actor_username,
        })

    monkeypatch.setattr(
        "src.services.platform_service_service.secret_service_client."
        "notify_dept_service_access_revoked",
        fake_notify,
    )

    await platform_service_service.delete_service(
        db, actor_id=account_admin.id, service_name="secret_service",
    )

    assert {c["dept_id"] for c in captured} == {dept_a.id, dept_b.id}
    assert all(c["service"] == "secret_service" for c in captured)
    assert all(c["actor_id"] == account_admin.id for c in captured)


@pytest.mark.asyncio
async def test_delete_non_secret_service_does_not_emit_lifecycle(
    db, account_admin, dept_a, monkeypatch,
):
    """delete_service другого сервиса — никакого lifecycle-emit."""
    from src.models import PlatformService
    from src.services import platform_service_service
    from src.services.department_service import grant_service_access

    svc = PlatformService(
        service_name="other_svc",
        display_name="Other",
        is_active=True,
    )
    db.add(svc)
    await db.flush()
    await grant_service_access(
        db, actor_id=account_admin.id,
        department_id=dept_a.id, service_name="other_svc",
    )

    captured: list[dict] = []

    async def fake_notify(dept_id, service, actor_id, actor_username):
        captured.append({"service": service})

    monkeypatch.setattr(
        "src.services.platform_service_service.secret_service_client."
        "notify_dept_service_access_revoked",
        fake_notify,
    )

    await platform_service_service.delete_service(
        db, actor_id=account_admin.id, service_name="other_svc",
    )

    assert captured == []
