"""update_bot: сужение allowed_services без ролей → нет audit, removed_role_count=0.

Дополнение к test_bot_update_purges_roles.py: граничные случаи очистки ролей
при сужении allowed_services.
"""

import pytest

BOTS_URL = "/api/auth/v1/bots"
SERVICES_URL = "/api/auth/v1/services"


async def _grant_service_to_dept(db, dept_id, service_name):
    from src.models import DepartmentServiceAccess, PlatformService, ServiceRoleDefinition
    from src.utils.ids import _new_id, service_role_def_id

    svc = await db.get(PlatformService, service_name)
    if svc is None:
        svc = PlatformService(
            service_name=service_name, display_name=service_name.title(), is_active=True,
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


def _capture(monkeypatch):
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


async def test_narrowing_with_no_roles_still_emits_purge_audit(
    client, db, admin_token, dept_a_with_service, service_x, monkeypatch,
):
    """Сужение allowed_services у бота без ролей: removed_role_count=0, purge audit всё равно эмитится.

    Гейт в `bot_service` — `if removed_services:` (список не пуст), а не
    `if removed_role_count > 0`. Сам факт сужения allowed_services уже
    подлежит аудиту: SIEM хочет видеть, что у бота отняли сервис, даже
    если ролей под него ещё не выдали. Поле `removed_role_count=0`
    остаётся в details — оператор по нему отличит «сужение без потерь»
    от «сужение с purge ролей».
    """
    captured = _capture(monkeypatch)
    await _grant_service_to_dept(db, dept_a_with_service.id, "no_roles_svc")
    await db.commit()

    bot_resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "no_roles_narrow_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name, "no_roles_svc"],
        },
    )
    assert bot_resp.status_code == 201, bot_resp.text
    bot_id = bot_resp.json()["bot_id"]
    # Роли NOT assigned — бот пустой в части ролей.

    captured.clear()
    patch = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"allowed_services": [service_x.service_name]},
    )
    assert patch.status_code == 200, patch.text

    purge_events = [
        e for e in captured
        if e.get("action") == "bot.roles_purged_on_services_narrowed"
    ]
    assert purge_events, "purge audit ожидается даже без удалённых ролей"
    details = purge_events[-1]["details"]
    assert details["removed_role_count"] == 0
    assert details["removed_services"] == ["no_roles_svc"]


async def test_narrowing_all_services_purges_all_roles(
    client, db, admin_token, dept_a_with_service, service_x, monkeypatch,
):
    """Убираем единственный сервис с ролями → removed_role_count > 0, purge audit."""
    captured = _capture(monkeypatch)

    bot_resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "all_narrow_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name],
        },
    )
    assert bot_resp.status_code == 201, bot_resp.text
    bot_id = bot_resp.json()["bot_id"]

    assign = await client.post(
        f"{BOTS_URL}/{bot_id}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader", "operator"]},
    )
    assert assign.status_code == 201, assign.text

    captured.clear()
    patch = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"allowed_services": []},
    )
    assert patch.status_code == 200, patch.text

    purge_events = [
        e for e in captured
        if e.get("action") == "bot.roles_purged_on_services_narrowed"
    ]
    assert purge_events, f"purge audit должен быть при удалении сервиса с ролями"
    details = purge_events[-1]["details"]
    assert details["removed_role_count"] == 2
    assert service_x.service_name in details["removed_services"]


async def test_narrowing_multiple_services_counts_all_removed_roles(
    client, db, admin_token, dept_a_with_service, service_x, monkeypatch,
):
    """Убираем два сервиса с ролями → removed_role_count = сумма ролей обоих."""
    captured = _capture(monkeypatch)
    await _grant_service_to_dept(db, dept_a_with_service.id, "svc_multi_a")
    await _grant_service_to_dept(db, dept_a_with_service.id, "svc_multi_b")
    await db.commit()

    bot_resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "multi_narrow_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name, "svc_multi_a", "svc_multi_b"],
        },
    )
    assert bot_resp.status_code == 201, bot_resp.text
    bot_id = bot_resp.json()["bot_id"]

    for svc in ("svc_multi_a", "svc_multi_b"):
        assign = await client.post(
            f"{BOTS_URL}/{bot_id}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": svc, "roles": ["reader"]},
        )
        assert assign.status_code == 201, assign.text

    captured.clear()
    patch = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"allowed_services": [service_x.service_name]},
    )
    assert patch.status_code == 200, patch.text

    purge_events = [
        e for e in captured
        if e.get("action") == "bot.roles_purged_on_services_narrowed"
    ]
    assert len(purge_events) == 1
    details = purge_events[-1]["details"]
    # Два сервиса убраны, по одной роли на каждый — итого 2.
    assert details["removed_role_count"] == 2
    assert sorted(details["removed_services"]) == ["svc_multi_a", "svc_multi_b"]


async def test_same_allowed_services_patch_no_purge(
    client, db, admin_token, dept_a_with_service, service_x, monkeypatch,
):
    """PATCH allowed_services с теми же значениями → removed_services пуст, purge нет."""
    captured = _capture(monkeypatch)

    bot_resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "same_svc_patch_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name],
        },
    )
    bot_id = bot_resp.json()["bot_id"]
    await client.post(
        f"{BOTS_URL}/{bot_id}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )

    captured.clear()
    patch = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"allowed_services": [service_x.service_name]},
    )
    assert patch.status_code == 200, patch.text

    purge_events = [
        e for e in captured
        if e.get("action") == "bot.roles_purged_on_services_narrowed"
    ]
    assert not purge_events, (
        "тот же список allowed_services не должен порождать purge audit"
    )
