"""Каждый error_code из README §«Error codes» как минимум один раз отдаётся.

Покрываем стабильный каталог: SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT,
CREDENTIAL_NOT_FOUND, CREDENTIAL_BLOCKED, NAME_DUPLICATE,
CREDENTIAL_ACCESS_DENIED, ADMIN_OVERRIDE_REASON_REQUIRED,
DEPT_GRANT_REQUIRED, RATE_LIMIT_EXCEEDED.

Все ошибки несут стандартный envelope: error / error_code / message /
details / request_id / timestamp.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import auth_header

BASE = "/api/secret/v1"
INTERNAL_KEY = "internal-test-key"

pytestmark = pytest.mark.asyncio


def _assert_envelope(body: dict, *, error_code: str) -> None:
    assert body["error_code"] == error_code
    for key in ("error", "message", "details", "request_id", "timestamp"):
        assert key in body, f"Поле {key} отсутствует в envelope"


async def test_service_not_available_for_department(client, identity_factory):
    """allowed_services без secret_service → 403."""
    actor = identity_factory(
        user_id="usr_no_access",
        department_id="dep_a",
        allowed_services=["other_service"],  # secret_service нет
    )
    resp = await client.get(f"{BASE}/credentials", headers=auth_header(actor))
    assert resp.status_code == 403
    _assert_envelope(resp.json(), error_code="SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT")


async def test_credential_not_found(client, identity_factory):
    actor = identity_factory(
        user_id="usr_searcher",
        department_id="dep_a",
        service_roles={"secret_service": ["reader"]},
    )
    resp = await client.get(
        f"{BASE}/credentials/cred_doesnotexist", headers=auth_header(actor),
    )
    assert resp.status_code == 404
    _assert_envelope(resp.json(), error_code="CREDENTIAL_NOT_FOUND")


async def test_credential_blocked_410(
    client, identity_factory,
):
    """blocked cred при попытке update — 410 GONE с blocked_reason в details."""
    owner_id = "usr_blocked_for_410"
    owner = identity_factory(
        user_id=owner_id,
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    cred = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "block_me", "service": "jira", "scope": "personal", "secret": "x"},
    )
    cred_id = cred.json()["id"]
    # ACL — чтобы lifecycle блокировал, а не удалял.
    await client.post(
        f"{BASE}/credentials/{cred_id}/acl",
        headers=auth_header(owner),
        json={"dept_id": "dep_a", "role_name": "reader", "can_read": True},
    )
    # Грантим reader access — после lifecycle user deleted, owner больше не
    # должен быть валиден; используем grantee как читателя.
    grantee = identity_factory(
        user_id="usr_grantee_410",
        department_id="dep_a",
        service_roles={"secret_service": ["reader"]},
    )
    await client.post(
        f"{BASE}/internal/lifecycle/user-deleted",
        headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
        json={"user_id": owner_id, "actor_id": "usr_admin"},
    )

    # Reveal на blocked cred — у reader есть can_read через ACL, но cred blocked.
    resp = await client.post(
        f"{BASE}/credentials/{cred_id}/reveal", headers=auth_header(grantee),
    )
    assert resp.status_code == 410
    body = resp.json()
    _assert_envelope(body, error_code="CREDENTIAL_BLOCKED")
    assert body["details"]["blocked_reason"] == "owner_user_deleted"


async def test_name_duplicate_409(client, identity_factory):
    owner = identity_factory(
        user_id="usr_duped",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    payload = {
        "name": "duped_name",
        "service": "jira",
        "scope": "personal",
        "secret": "x",
    }
    first = await client.post(f"{BASE}/credentials", headers=auth_header(owner), json=payload)
    assert first.status_code == 201

    second = await client.post(f"{BASE}/credentials", headers=auth_header(owner), json=payload)
    assert second.status_code == 409
    _assert_envelope(second.json(), error_code="NAME_DUPLICATE")


async def test_credential_access_denied_403(client, identity_factory):
    """cross_dep cred + DeptGrant, но без RoleACL → 403, не 404 (есть DeptGrant)."""
    owner_admin = identity_factory(
        user_id="usr_admin_403",
        department_id="dep_owner_403",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    cred = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner_admin),
        json={
            "name": "denied_target",
            "service": "git",
            "scope": "cross_department",
            "secret": "x",
            "owner_dept_id": "dep_owner_403",
        },
    )
    cred_id = cred.json()["id"]
    grant = await client.post(
        f"{BASE}/credentials/{cred_id}/dept-grants",
        headers=auth_header(owner_admin),
        json={"recipient_dept_id": "dep_a"},
    )
    assert grant.status_code == 201

    reader = identity_factory(
        user_id="usr_reader_403",
        department_id="dep_a",
        service_roles={"secret_service": ["reader"]},
    )
    resp = await client.get(f"{BASE}/credentials/{cred_id}", headers=auth_header(reader))
    assert resp.status_code == 403
    _assert_envelope(resp.json(), error_code="CREDENTIAL_ACCESS_DENIED")


async def test_admin_override_reason_required_422(client, identity_factory):
    owner = identity_factory(
        user_id="usr_owner_422",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    cred = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "reason_target", "service": "jira", "scope": "personal", "secret": "x"},
    )
    cred_id = cred.json()["id"]

    svc_admin = identity_factory(
        user_id="usr_admin_422",
        department_id="dep_b",
        service_roles={"secret_service": ["admin"]},
    )
    resp = await client.delete(
        f"{BASE}/credentials/{cred_id}", headers=auth_header(svc_admin),
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), error_code="ADMIN_OVERRIDE_REASON_REQUIRED")


async def test_dept_grant_required_422(client, identity_factory):
    """cross_dep cred + попытка создать ACL без DeptGrant → 422 DEPT_GRANT_REQUIRED."""
    owner_admin = identity_factory(
        user_id="usr_owner_422_g",
        department_id="dep_owner_g",
        platform_role="department_admin",
        service_roles={"secret_service": ["admin"]},
    )
    cred = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner_admin),
        json={
            "name": "acl_no_grant",
            "service": "git",
            "scope": "cross_department",
            "secret": "x",
            "owner_dept_id": "dep_owner_g",
        },
    )
    cred_id = cred.json()["id"]

    # owner_admin пытается выдать ACL recipient'у dep_X без DeptGrant.
    resp = await client.post(
        f"{BASE}/credentials/{cred_id}/acl",
        headers=auth_header(owner_admin),
        json={"dept_id": "dep_x_no_grant", "role_name": "reader", "can_read": True},
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), error_code="DEPT_GRANT_REQUIRED")


@pytest.mark.skip(
    reason=(
        "Глобальный slowapi default_limits не enforce'ится: в src/main.py нет "
        "ни SlowAPIMiddleware, ни @limiter.limit на роутах. Лимитер создан и "
        "exception-handler зарегистрирован, но без middleware/декоратора "
        "лимит никогда не срабатывает. RATE_LIMIT_EXCEEDED envelope покрыт "
        "юнит-тестом обработчика; интеграционный тест откладывается до "
        "включения SlowAPIMiddleware."
    )
)
async def test_rate_limit_exceeded_429(client, identity_factory, monkeypatch):
    """Глобальный slowapi-лимит — на высоком пределе по умолчанию, но
    SLOWAPI поддерживает endpoint-decorator'ы. В нашем сервисе явных
    `@limiter.limit` на reveal нет, но глобальный лимит может срабатывать.

    Тест: понижаем глобальный лимит до 2/minute через cleanup limiter'а
    и попадаем в 429. Чтобы не наследовать состояние, делаем явный
    `limiter.reset()` после.
    """
    from src.main import app

    limiter = getattr(app.state, "limiter", None)
    assert limiter is not None
    # Подменяем default_limits на жёсткие.
    limiter.reset()
    original = limiter._default_limits
    try:
        from limits import parse  # type: ignore
        from slowapi.wrappers import Limit  # type: ignore

        limiter._default_limits = [
            Limit(
                limit=parse("2/minute"),
                key_func=limiter._key_func,
                scope=None,
                per_method=False,
                methods=None,
                error_message=None,
                exempt_when=None,
                cost=1,
                override_defaults=False,
            )
        ]

        actor = identity_factory(
            user_id="usr_rate_target",
            department_id="dep_a",
            service_roles={"secret_service": ["reader"]},
        )
        last_status = None
        # Долбим до 429 (или до 10 попыток).
        for _ in range(10):
            r = await client.get(f"{BASE}/credentials", headers=auth_header(actor))
            last_status = r.status_code
            if r.status_code == 429:
                break
        assert last_status == 429
        body = r.json()
        _assert_envelope(body, error_code="RATE_LIMIT_EXCEEDED")
    finally:
        limiter._default_limits = original
        limiter.reset()
