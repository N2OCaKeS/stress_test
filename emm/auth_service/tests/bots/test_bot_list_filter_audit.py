"""list_bots audit отделяет requested-фильтр от фактически применённого.

Для department_admin query-param `?department_id=…` всегда форсится на отдел
актора (cross-tenant запрещён сервисным слоем). Раньше в audit писали только
то, что прислал клиент (`filter_department_id`) — SIEM не мог отличить, что
dept_admin пытался прочитать чужой отдел и был тихо переадресован на свой.

Сейчас в `details` идут оба ключа:
* `filter_department_id_requested` — из query, как есть;
* `filter_department_id_effective` — что реально подставили в SQL.
"""

import pytest

from tests._helpers.http import _create_bot  # noqa: F401 — общий helper

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


async def test_dept_admin_cross_tenant_filter_audited_with_both_keys(
    client, admin_token, dept_admin_a_token, dept_a, dept_b, captured_audit,
):
    """dept_admin (dept_a) шлёт ?department_id=dept_b → отдаём только своих
    + audit содержит requested=dept_b / effective=dept_a."""
    await _create_bot(client, admin_token, dept_a.id, "audit_bot_a")
    await _create_bot(client, admin_token, dept_b.id, "audit_bot_b")

    captured_audit.clear()

    resp = await client.get(
        BOTS_URL,
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        params={"department_id": dept_b.id},
    )
    assert resp.status_code == 200
    names = {b["name"] for b in resp.json()}
    assert "audit_bot_a" in names
    assert "audit_bot_b" not in names

    list_events = [e for e in captured_audit if e.get("action") == "bot.list"]
    assert list_events, f"bot.list audit not emitted: {captured_audit}"
    ev = list_events[-1]
    details = ev.get("details") or {}
    assert details.get("filter_department_id_requested") == dept_b.id, details
    assert details.get("filter_department_id_effective") == dept_a.id, details
    assert details["filter_department_id_requested"] != details["filter_department_id_effective"]


async def test_admin_filter_matches_effective_value(
    client, admin_token, dept_a, captured_audit,
):
    """account_admin: requested == effective (никакого override-а)."""
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
    assert details.get("filter_department_id_requested") == dept_a.id
    assert details.get("filter_department_id_effective") == dept_a.id


async def test_admin_no_filter_effective_is_none(
    client, admin_token, captured_audit,
):
    """account_admin без фильтра → effective=None, scope=all."""
    captured_audit.clear()

    resp = await client.get(
        BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    list_events = [e for e in captured_audit if e.get("action") == "bot.list"]
    assert list_events, captured_audit
    details = list_events[-1].get("details") or {}
    assert details.get("filter_department_id_requested") is None
    assert details.get("filter_department_id_effective") is None
    assert details.get("scope") == "all"
