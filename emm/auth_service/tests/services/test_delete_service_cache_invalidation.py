"""DELETE /services/{name} должен сбрасывать identity-cache юзеров с прямой ролью.

До фикса инвалидация шла только по ботам — юзеры до истечения TTL (5s) могли
продолжать видеть удалённую роль в introspect.
"""

URL = "/api/auth/v1/services"


async def test_delete_service_invalidates_identity_cache_for_role_holders(
    client, admin_token, user_a, service_x, monkeypatch,
):
    invalidated: list[str] = []

    def _spy(subject_id):
        invalidated.append(subject_id)

    monkeypatch.setattr(
        "src.services.platform_service_service._invalidate_identity_cache", _spy
    )

    resp = await client.delete(
        f"{URL}/{service_x.service_name}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert user_a.id in invalidated, (
        f"identity-cache юзера с прямой ролью должен быть сброшен, got {invalidated}"
    )


async def test_delete_service_audit_includes_affected_user_count(
    client, admin_token, user_a, service_x, capture_audit_payloads,
):
    resp = await client.delete(
        f"{URL}/{service_x.service_name}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    deletes = [p for p in capture_audit_payloads if p["action"] == "service.delete"]
    assert deletes, "audit-событие должно эмититься"
    assert deletes[-1]["details"].get("affected_user_count", 0) >= 1


async def test_delete_service_emits_per_dept_service_revoke(
    client, admin_token, user_a, dept_a_with_service, service_x, capture_audit_payloads,
):
    """Снятие сервиса с регистрации даёт per-dept `department.service_revoke`
    для каждого отдела с активным доступом — SIEM видит трейл, а не только
    агрегатный счётчик в `service.delete`."""
    resp = await client.delete(
        f"{URL}/{service_x.service_name}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text

    revokes = [
        p for p in capture_audit_payloads
        if p["action"] == "department.service_revoke"
    ]
    assert revokes, "должно быть per-dept revoke-событие при удалении сервиса"
    dept_revoke = [
        p for p in revokes
        if p.get("target_id") == dept_a_with_service.id
        and p["details"].get("service_name") == service_x.service_name
    ]
    assert dept_revoke, f"нет revoke для dept {dept_a_with_service.id}: {revokes}"
    assert dept_revoke[-1]["details"].get("via") == "service.delete"
