"""End-to-end проверка работоспособности бота:

* создание в отделе → привязка к department_id;
* выдача токена → токен принимается через introspect;
* `service_roles` назначаются и применяются в `service-access`;
* `allowed_services` фильтруется по реальному dept-доступу;
* audit-события идут с `actor_type=bot` и `subject_id=bot.id`.

Сетевые вызовы audit_service.emit перехватываются через monkeypatch — события
просто складываются в список и проверяются.
"""

import pytest

BOTS_URL = "/api/auth/v1/bots"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
ACCESS_URL = "/api/auth/v1/authorization/service-access"


# ── Перехват audit-payloads (in-process, без запущенного logging_service) ─────

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
            class R: status_code = 201
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


# ── Полный сценарий жизни бота ────────────────────────────────────────────────

async def test_bot_full_lifecycle(client, admin_token, dept_a_with_service, service_x):
    """admin создаёт → выдаёт токен и роль → бот-токен принимается, права действуют."""
    # 1. Создаём бота, привязанного к отделу A с допуском к service_x.
    create = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "lifecycle_bot",
            "department_id": dept_a_with_service.id,
            "allowed_services": [service_x.service_name],
        },
    )
    assert create.status_code == 201
    bot = create.json()
    assert bot["department_id"] == dept_a_with_service.id
    assert bot["allowed_services"] == [service_x.service_name]
    bot_id = bot["bot_id"]

    # 2. Выдаём боту токен.
    tok = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "primary"},
    )
    assert tok.status_code == 201
    bot_token = tok.json()["token"]
    assert bot_token.startswith("dbos_bot_")

    # 3. Назначаем боту роль reader в service_x.
    assign = await client.post(
        f"{BOTS_URL}/{bot_id}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )
    assert assign.status_code == 201

    # 4. Bot-токен принимается introspect → правильный субъект, dept, allowed, roles.
    intr = await client.post(INTROSPECT_URL, json={"token": bot_token})
    body = intr.json()
    assert body["active"] is True
    assert body["subject_type"] == "bot"
    assert body["sub"] == bot_id
    assert body["department_id"] == dept_a_with_service.id
    assert body["allowed_services"] == [service_x.service_name]
    assert body["service_roles"] == {service_x.service_name: ["reader"]}

    # 5. service-access для service_x возвращает allowed=True + роли.
    access_ok = await client.post(
        ACCESS_URL,
        json={"subject_token": bot_token, "service_name": service_x.service_name},
    )
    assert access_ok.status_code == 200
    assert access_ok.json()["allowed"] is True
    assert "reader" in access_ok.json()["service_roles"]

    # 6. service-access для другого имени — отказ (нет в allowed_services).
    access_deny = await client.post(
        ACCESS_URL,
        json={"subject_token": bot_token, "service_name": "non_existent_svc"},
    )
    assert access_deny.json()["allowed"] is False


# ── Department-привязка ───────────────────────────────────────────────────────

async def test_bot_department_binding_is_propagated(
    client, admin_token, dept_a_with_service, dept_b, service_x,
):
    """department_id из ответа на create_bot совпадает с тем, что вернёт introspect."""
    bot = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "dept_bind_bot", "department_id": dept_a_with_service.id,
              "allowed_services": [service_x.service_name]},
    )).json()
    raw = (await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "t"},
    )).json()["token"]

    intr = (await client.post(INTROSPECT_URL, json={"token": raw})).json()
    assert intr["department_id"] == dept_a_with_service.id
    # Не вижу dept_b в outputе → бот изолирован от чужого отдела.
    assert intr["department_id"] != dept_b.id


async def test_bot_loses_service_after_dept_revoke(
    client, admin_token, dept_a_with_service, service_x,
):
    """После revoke дептартамент→сервис бот теряет сервис в effective_services и
    его service_roles очищаются."""
    bot = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "revoke_bot", "department_id": dept_a_with_service.id,
              "allowed_services": [service_x.service_name]},
    )).json()
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["operator"]},
    )
    raw = (await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "before"},
    )).json()["token"]
    assert (await client.post(INTROSPECT_URL, json={"token": raw})).json()["allowed_services"] == [
        service_x.service_name
    ]

    # Revoke dept→service
    await client.delete(
        f"/api/auth/v1/departments/{dept_a_with_service.id}/services/{service_x.service_name}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    after = (await client.post(INTROSPECT_URL, json={"token": raw})).json()
    assert after["allowed_services"] == []
    assert after["service_roles"] == {}

    access = (await client.post(
        ACCESS_URL,
        json={"subject_token": raw, "service_name": service_x.service_name},
    )).json()
    assert access["allowed"] is False


# ── Логирование от имени бота ─────────────────────────────────────────────────

async def test_introspect_emits_audit_with_actor_type_bot(
    client, admin_token, dept_a_with_service, service_x, captured_audit,
):
    """Когда боту делают introspect/service-access — события идут с
    actor_type='bot' и subject_id=bot.id, не как от user."""
    bot = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "audit_bot", "department_id": dept_a_with_service.id,
              "allowed_services": [service_x.service_name]},
    )).json()
    raw = (await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "audit_tok"},
    )).json()["token"]

    captured_audit.clear()  # отбросить события setup-фазы
    await client.post(INTROSPECT_URL, json={"token": raw})
    await client.post(
        ACCESS_URL,
        json={"subject_token": raw, "service_name": service_x.service_name},
    )

    introspect_events = [e for e in captured_audit if e["action"] == "token.introspect"]
    assert introspect_events, "token.introspect event must be emitted"
    bot_introspect = [e for e in introspect_events if e.get("actor_type") == "bot"]
    assert bot_introspect, f"expected at least one introspect event with actor_type=bot, got: {introspect_events}"
    assert bot_introspect[0]["actor_id"] == bot["bot_id"]
    assert bot_introspect[0]["department_id"] == dept_a_with_service.id
    assert bot_introspect[0]["details"]["token_type"] == "bot_token"

    access_events = [e for e in captured_audit if e["action"] == "service.access_check"]
    bot_access = [e for e in access_events if e["actor_id"] == bot["bot_id"]]
    assert bot_access, "service.access_check event must be emitted for the bot"
    assert bot_access[0]["allowed"] is True


async def test_bot_create_and_role_assign_audit_targets_bot(
    client, admin_token, dept_a_with_service, service_x, captured_audit,
):
    """Действия admin'а над ботом дают audit-event с target=bot — нужно для аудита
    кто/когда менял бота."""
    captured_audit.clear()
    bot = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "audited_bot", "department_id": dept_a_with_service.id,
              "allowed_services": [service_x.service_name]},
    )).json()
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )

    create_events = [e for e in captured_audit if e["action"] == "bot.create"]
    assert len(create_events) == 1
    assert create_events[0]["target_id"] == bot["bot_id"]
    assert create_events[0]["target_type"] == "bot"
    assert create_events[0]["details"]["department_id"] == dept_a_with_service.id

    role_events = [e for e in captured_audit if e["action"] == "bot.roles_assign"]
    assert len(role_events) == 1
    assert role_events[0]["target_id"] == bot["bot_id"]
    assert role_events[0]["details"]["service_name"] == service_x.service_name
    assert role_events[0]["details"]["roles"] == ["reader"]
