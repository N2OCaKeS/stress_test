"""update_bot со сужением allowed_services чистит BotServiceRole для ушедших сервисов.

До фикса роли оставались призраками: GET `/bots/{id}/roles` возвращал их,
introspect не отдавал (через intersect с allowed_services), и БД копила
бесхозные строки. Заодно публикуем audit `bot.roles_purged_on_services_narrowed`.
"""

import pytest

BOTS_URL = "/api/auth/v1/bots"
SERVICES_URL = "/api/auth/v1/services"


@pytest.fixture()
def captured_audit(monkeypatch):
    captured: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):
        captured.append(json)

    class _AsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json, headers):
            captured.append(json)
            class R:
                status_code = 201
            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.post", fake_sync_post)
    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type("S", (), {
            "logging_service_url": "http://test",
            "logging_service_api_key": "k",
        })(),
    )
    return captured


async def _grant_service_to_dept(db, dept_id, service_name):
    from src.models import DepartmentServiceAccess, PlatformService, ServiceRoleDefinition
    from src.utils.ids import _new_id, service_role_def_id

    svc = await db.get(PlatformService, service_name)
    if svc is None:
        svc = PlatformService(
            service_name=service_name, display_name=service_name.title(),
            is_active=True,
        )
        db.add(svc)
        await db.flush()
    db.add(DepartmentServiceAccess(
        id=_new_id("dsa_"), department_id=dept_id,
        service_name=service_name, is_active=True,
    ))
    for role in ("admin", "reader", "operator", "guest"):
        db.add(ServiceRoleDefinition(
            id=service_role_def_id(),
            department_id=dept_id, service_name=service_name,
            role_name=role, display_name=role.title(),
            is_active=True, is_system=(role == "admin"),
        ))
    await db.flush()


async def test_narrowing_allowed_services_purges_roles_and_emits_audit(
    client, db, admin_token, dept_a_with_service, service_x, captured_audit,
):
    # Второй сервис в том же отделе, чтобы у бота было два allowed_services.
    await _grant_service_to_dept(db, dept_a_with_service.id, "service_y")
    await db.commit()

    create = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "purge_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name, "service_y"],
        },
    )
    assert create.status_code == 201, create.text
    bot_id = create.json()["bot_id"]

    # Раздаём ролей в обоих сервисах.
    for svc in (service_x.service_name, "service_y"):
        assign = await client.post(
            f"{BOTS_URL}/{bot_id}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": svc, "roles": ["reader"]},
        )
        assert assign.status_code == 201, assign.text

    # Сужаем allowed_services — service_y выкинут.
    captured_audit.clear()
    patch = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"allowed_services": [service_x.service_name]},
    )
    assert patch.status_code == 200, patch.text

    # Роли на service_y должны исчезнуть.
    roles_resp = await client.get(
        f"{BOTS_URL}/{bot_id}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert roles_resp.status_code == 200
    services_in_roles = {r["service_name"] for r in roles_resp.json()}
    assert "service_y" not in services_in_roles
    assert service_x.service_name in services_in_roles

    # Audit-событие с правильными деталями.
    purge_events = [
        e for e in captured_audit
        if e.get("action") == "bot.roles_purged_on_services_narrowed"
    ]
    assert purge_events, f"purge audit not emitted: {captured_audit}"
    details = purge_events[-1].get("details") or {}
    assert details.get("bot_id") == bot_id
    assert details.get("removed_services") == ["service_y"]
    assert details.get("removed_role_count") == 1


async def test_widening_allowed_services_keeps_existing_roles(
    client, db, admin_token, dept_a_with_service, service_x, captured_audit,
):
    """Расширение allowed_services не должно ничего чистить."""
    await _grant_service_to_dept(db, dept_a_with_service.id, "service_z")
    await db.commit()

    create = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "widen_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name],
        },
    )
    bot_id = create.json()["bot_id"]

    await client.post(
        f"{BOTS_URL}/{bot_id}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )

    captured_audit.clear()
    patch = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"allowed_services": [service_x.service_name, "service_z"]},
    )
    assert patch.status_code == 200, patch.text

    purge_events = [
        e for e in captured_audit
        if e.get("action") == "bot.roles_purged_on_services_narrowed"
    ]
    assert not purge_events
