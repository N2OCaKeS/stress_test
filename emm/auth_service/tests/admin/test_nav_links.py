"""Тесты: настраиваемая кнопка левой панели web-UI (`/nav-links`).

GET /nav-links фильтрует по отделу identity (виден только разрешённым отделам).
PUT /admin/nav-links — только account_admin (иначе 403), валидирует url,
`enabled=False` → пустая выдача. Аудит-событие `nav_link.update` (INFO).
"""

import pytest

from src.services.audit_events import SERVICE_EVENTS

PUBLIC_URL = "/api/auth/v1/nav-links"
ADMIN_URL = "/api/auth/v1/admin/nav-links"


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_nav_link_event_is_registered_info():
    ev = next(e for e in SERVICE_EVENTS if e["action"] == "nav_link.update")
    assert ev["default_severity"] == "INFO"


# ── admin GET/PUT roundtrip ─────────────────────────────────────────────────────

async def test_get_config_defaults_when_no_row(client, admin_token):
    resp = await client.get(ADMIN_URL, headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is False
    assert body["label"] == "allta"
    assert body["url"] is None
    assert body["all_departments"] is False
    assert body["department_ids"] == []


async def test_put_then_get_roundtrip(client, admin_token, dept_a):
    put = await client.put(
        ADMIN_URL,
        headers=_h(admin_token),
        json={
            "enabled": True,
            "label": "Allta App",
            "url": "https://allta.example.ru/",
            "all_departments": False,
            "department_ids": [dept_a.id, dept_a.id],
        },
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["enabled"] is True
    assert body["label"] == "Allta App"
    assert body["url"] == "https://allta.example.ru/"
    # Дубликаты отдела схлопнулись.
    assert body["department_ids"] == [dept_a.id]

    get = await client.get(ADMIN_URL, headers=_h(admin_token))
    assert get.json()["url"] == "https://allta.example.ru/"


async def test_put_emits_audit(client, admin_token, capture_audit_payloads):
    await client.put(
        ADMIN_URL,
        headers=_h(admin_token),
        json={
            "enabled": True,
            "label": "allta",
            "url": "https://allta.example.ru/",
            "all_departments": True,
            "department_ids": [],
        },
    )
    ev = next(p for p in capture_audit_payloads if p["action"] == "nav_link.update")
    assert ev["details"]["enabled"] is True
    assert ev["details"]["all_departments"] is True


# ── validation ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url",
    ["ftp://x", "javascript:alert(1)", "not-a-url", "/relative/path"],
)
async def test_put_rejects_non_http_url(client, admin_token, url):
    resp = await client.put(
        ADMIN_URL,
        headers=_h(admin_token),
        json={"enabled": True, "label": "allta", "url": url, "all_departments": True, "department_ids": []},
    )
    assert resp.status_code == 422, resp.text


async def test_put_enabled_without_url_rejected(client, admin_token):
    resp = await client.put(
        ADMIN_URL,
        headers=_h(admin_token),
        json={"enabled": True, "label": "allta", "url": None, "all_departments": True, "department_ids": []},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == "NAV_LINK_URL_REQUIRED"


# ── authz on admin endpoints ─────────────────────────────────────────────────────

async def test_admin_get_forbidden_for_dept_admin(client, dept_admin_a_token):
    resp = await client.get(ADMIN_URL, headers=_h(dept_admin_a_token))
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_REQUIRED"


async def test_admin_put_forbidden_for_dept_admin(client, dept_admin_a_token):
    resp = await client.put(
        ADMIN_URL,
        headers=_h(dept_admin_a_token),
        json={"enabled": True, "label": "allta", "url": "https://x.example.ru/", "all_departments": True, "department_ids": []},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ROLE_REQUIRED"


# ── public GET: per-department visibility ────────────────────────────────────────

async def test_public_get_filters_by_department(
    client, admin_token, user_a_token, user_b_token, dept_a
):
    # Кнопка видна только отделу dept_a (user_a), не user_b.
    await client.put(
        ADMIN_URL,
        headers=_h(admin_token),
        json={
            "enabled": True,
            "label": "allta",
            "url": "https://allta.example.ru/",
            "all_departments": False,
            "department_ids": [dept_a.id],
        },
    )

    a = await client.get(PUBLIC_URL, headers=_h(user_a_token))
    assert a.status_code == 200, a.text
    assert a.json() == [{"label": "allta", "url": "https://allta.example.ru/"}]

    b = await client.get(PUBLIC_URL, headers=_h(user_b_token))
    assert b.status_code == 200, b.text
    assert b.json() == []


async def test_public_get_all_departments_visible_to_everyone(
    client, admin_token, user_a_token, user_b_token
):
    await client.put(
        ADMIN_URL,
        headers=_h(admin_token),
        json={
            "enabled": True,
            "label": "allta",
            "url": "https://allta.example.ru/",
            "all_departments": True,
            "department_ids": [],
        },
    )
    for token in (user_a_token, user_b_token):
        r = await client.get(PUBLIC_URL, headers=_h(token))
        assert r.status_code == 200, r.text
        assert r.json() == [{"label": "allta", "url": "https://allta.example.ru/"}]


async def test_public_get_empty_when_disabled(
    client, admin_token, user_a_token, dept_a
):
    # Даже с all_departments — если выключено, выдача пуста.
    await client.put(
        ADMIN_URL,
        headers=_h(admin_token),
        json={
            "enabled": False,
            "label": "allta",
            "url": "https://allta.example.ru/",
            "all_departments": True,
            "department_ids": [dept_a.id],
        },
    )
    r = await client.get(PUBLIC_URL, headers=_h(user_a_token))
    assert r.status_code == 200, r.text
    assert r.json() == []
