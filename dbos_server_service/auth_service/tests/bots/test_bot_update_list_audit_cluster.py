"""Сборник проверок по кластеру правок update_bot / list_bots audit:

* update_bot для cross-tenant dept_admin'а эмитит failure-audit
  (а не просто raise) — симметрия с прочими bot-функциями;
* при PATCH'е `status` audit.details.changes не содержит derived
  `is_active` — SIEM/оператор видит только то, что прислал caller;
* `bot.list` audit несёт `count_disabled` (сколько inactive в выдаче);
* cross-tenant denied-audit под status=`failure` (не `denied`) —
  единая конвенция auth_service.
"""

import pytest

BOTS_URL = "/api/auth/v1/bots"


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


async def _create_bot(client, token, dept_id, name):
    return await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": []},
    )


async def test_update_bot_cross_tenant_emits_failure_audit(
    client, admin_token, dept_admin_a_token, dept_b, captured_audit,
):
    """dept_admin (dept_a) патчит чужого бота (dept_b) → 403 + failure-audit."""
    bot_id = (await _create_bot(client, admin_token, dept_b.id, "w12_xt_bot")).json()["bot_id"]
    captured_audit.clear()

    resp = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"description": "hijack"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BOT_UPDATE_FORBIDDEN"

    failed = [
        e for e in captured_audit
        if e.get("action") == "bot.update" and e.get("status") == "failure"
    ]
    assert failed, f"no failure-audit for bot.update: {captured_audit}"
    details = failed[0].get("details") or {}
    assert details["reason"] == "cross_department_bot"
    assert details["bot_id"] == bot_id


async def test_update_bot_status_audit_changes_no_derived_is_active(
    client, admin_token, dept_a, captured_audit,
):
    """PATCH {status: disabled} → audit.changes содержит только status, не is_active."""
    bot_id = (await _create_bot(client, admin_token, dept_a.id, "w12_audit_status")).json()["bot_id"]
    captured_audit.clear()

    resp = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "disabled"},
    )
    assert resp.status_code == 200

    update_events = [e for e in captured_audit if e.get("action") == "bot.update"]
    assert update_events, captured_audit
    details = update_events[-1].get("details") or {}
    changes = details.get("changes") or {}
    fields = details.get("fields_changed") or []
    assert "status" in changes
    assert changes["status"] == "disabled"
    assert "is_active" not in changes, f"derived is_active leaked into audit: {changes}"
    assert "is_active" not in fields


async def test_bot_list_audit_carries_count_disabled(
    client, admin_token, dept_a, captured_audit,
):
    """В bot.list audit есть `count_disabled` — сколько inactive в выдаче."""
    bot_id = (await _create_bot(client, admin_token, dept_a.id, "w12_list_active")).json()["bot_id"]
    bot_off_id = (await _create_bot(client, admin_token, dept_a.id, "w12_list_off")).json()["bot_id"]
    await client.patch(
        f"{BOTS_URL}/{bot_off_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "disabled"},
    )
    captured_audit.clear()

    resp = await client.get(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"department_id": dept_a.id},
    )
    assert resp.status_code == 200

    list_events = [e for e in captured_audit if e.get("action") == "bot.list"]
    assert list_events, captured_audit
    details = list_events[-1].get("details") or {}
    assert "count_disabled" in details
    # Минимум один disabled бот в выдаче (только что переключённый).
    assert details["count_disabled"] >= 1
    # И активных в выдаче тоже минимум один.
    assert details["count"] - details["count_disabled"] >= 1
    assert bot_id  # silence linter про unused var


async def test_update_bot_cross_tenant_failure_status_is_failure_not_denied(
    client, admin_token, dept_admin_a_token, dept_b, captured_audit,
):
    """Status конвенция: cross-tenant bot.update — `failure`, не `denied`."""
    bot_id = (await _create_bot(client, admin_token, dept_b.id, "w12_status_conv")).json()["bot_id"]
    captured_audit.clear()

    resp = await client.patch(
        f"{BOTS_URL}/{bot_id}",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        json={"description": "x"},
    )
    assert resp.status_code == 403

    update_events = [e for e in captured_audit if e.get("action") == "bot.update"]
    assert update_events, captured_audit
    # Ни одного denied — только failure.
    assert all(e.get("status") != "denied" for e in update_events), update_events
    assert any(e.get("status") == "failure" for e in update_events), update_events
