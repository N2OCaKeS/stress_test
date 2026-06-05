"""Тесты no-op-revoke ботовых ролей.

Закрывает P2 (BUG) из W12-аудита: `revoke_bot_roles` на сервисе, где у бота нет
ролей (или сервис не в `bot.allowed_services`) до фикса эмитил `bot.roles_revoke`
audit без реального изменения — SIEM получал ложный сигнал. После фикса
endpoint возвращает 200, но audit не эмитится.
"""

BOTS_URL = "/api/auth/v1/bots"


async def _create_bot(client, token, dept_id, *, name, services):
    resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": services},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_revoke_noop_does_not_emit_audit(
    client, admin_token, dept_a_with_service, service_x, monkeypatch,
):
    """Сервис в `bot.allowed_services`, но ролей бот не получал → DELETE
    возвращает 200, `bot.roles_revoke` НЕ эмитится."""
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="rb_noop_no_roles", services=[service_x.service_name],
    )

    captured: list[dict] = []
    from src.services import audit_service as _audit
    real_emit = _audit.emit

    def _spy(action, *args, **kwargs):
        if action == "bot.roles_revoke":
            captured.append({"action": action, "kwargs": kwargs})
        return real_emit(action, *args, **kwargs)

    monkeypatch.setattr(_audit, "emit", _spy)

    resp = await client.delete(
        f"{BOTS_URL}/{bot['bot_id']}/roles/{service_x.service_name}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured == [], (
        "no-op revoke не должен эмитить bot.roles_revoke (ложный SIEM-сигнал)"
    )


async def test_revoke_unknown_service_does_not_emit_audit(
    client, admin_token, dept_a_with_service, monkeypatch,
):
    """`service_name` вообще вне `bot.allowed_services` → DELETE 200,
    audit-event НЕ эмитится (revoke семантически no-op)."""
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="rb_noop_unknown_svc", services=[],
    )

    captured: list[dict] = []
    from src.services import audit_service as _audit
    real_emit = _audit.emit

    def _spy(action, *args, **kwargs):
        if action == "bot.roles_revoke":
            captured.append({"action": action, "kwargs": kwargs})
        return real_emit(action, *args, **kwargs)

    monkeypatch.setattr(_audit, "emit", _spy)

    resp = await client.delete(
        f"{BOTS_URL}/{bot['bot_id']}/roles/some_random_svc",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured == []


async def test_revoke_with_roles_still_emits_audit(
    client, admin_token, dept_a_with_service, service_x, monkeypatch,
):
    """Sanity: реальный revoke (роли были) — audit всё ещё эмитится."""
    bot = await _create_bot(
        client, admin_token, dept_a_with_service.id,
        name="rb_real_revoke", services=[service_x.service_name],
    )
    await client.post(
        f"{BOTS_URL}/{bot['bot_id']}/roles",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service_x.service_name, "roles": ["reader"]},
    )

    captured: list[dict] = []
    from src.services import audit_service as _audit
    real_emit = _audit.emit

    def _spy(action, *args, **kwargs):
        if action == "bot.roles_revoke":
            captured.append({"action": action, "kwargs": kwargs})
        return real_emit(action, *args, **kwargs)

    monkeypatch.setattr(_audit, "emit", _spy)

    resp = await client.delete(
        f"{BOTS_URL}/{bot['bot_id']}/roles/{service_x.service_name}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert len(captured) == 1
    assert captured[0]["kwargs"]["details"]["service_name"] == service_x.service_name
