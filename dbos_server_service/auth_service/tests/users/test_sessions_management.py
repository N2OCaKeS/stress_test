"""Тесты: /api/auth/v1/users/me/sessions — list / revoke-all / revoke-one."""

from tests._helpers.http import login as _login_full  # noqa: F401 — общий helper


LIST_URL = "/api/auth/v1/users/me/sessions"
REVOKE_URL = "/api/auth/v1/users/me/sessions/revoke"
REFRESH_URL = "/api/auth/v1/refresh"


# ── list_sessions ───────────────────────────────────────────────────────────


async def test_list_sessions_sees_own_active(client, user_a):
    """user_a логинится дважды → видит две свои активные сессии. is_current=True
    проставлено ровно у той сессии, чей access-токен использован для запроса.
    """
    s1 = await _login_full(client, "t_user_a", "User1234!")
    s2 = await _login_full(client, "t_user_a", "User1234!")

    resp = await client.get(
        LIST_URL, headers={"Authorization": f"Bearer {s2['access_token']}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2

    current_count = sum(1 for it in body["items"] if it["is_current"])
    assert current_count == 1, body
    # Ровно одна current; остальные — другие сессии того же юзера.
    for it in body["items"]:
        assert "session_id" in it
        assert "created_at" in it
        assert "expires_at" in it

    # Доп.проверка: тот же юзер дёрнул listing вторым токеном (s1) — теперь
    # current — другая сессия. Сессий уже 3, потому что s1 при первом use
    # last_used_at не обновляет (refresh не дёргали), но активной выписки 2
    # должно быть, плюс никаких лишних побочных сессий.
    resp2 = await client.get(
        LIST_URL, headers={"Authorization": f"Bearer {s1['access_token']}"}
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["total"] == 2


async def test_list_sessions_isolation_does_not_see_others(client, user_a, user_b):
    """user_a и user_b логинятся независимо; каждый видит ТОЛЬКО свои сессии."""
    await _login_full(client, "t_user_a", "User1234!")
    a2 = await _login_full(client, "t_user_a", "User1234!")
    await _login_full(client, "t_user_b", "User1234!")

    resp_a = await client.get(
        LIST_URL, headers={"Authorization": f"Bearer {a2['access_token']}"}
    )
    assert resp_a.status_code == 200
    body_a = resp_a.json()
    # user_a — 2 сессии, user_b в listing не виден.
    assert body_a["total"] == 2


async def test_list_sessions_requires_auth(client):
    resp = await client.get(LIST_URL)
    assert resp.status_code == 401


# ── revoke all sessions ────────────────────────────────────────────────────


async def test_revoke_all_clears_every_session_including_current(client, user_a):
    """except_current=false → revoke всех, refresh любым из RT возвращает 401."""
    s1 = await _login_full(client, "t_user_a", "User1234!")
    s2 = await _login_full(client, "t_user_a", "User1234!")

    resp = await client.post(
        REVOKE_URL,
        headers={"Authorization": f"Bearer {s2['access_token']}"},
        json={"except_current": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked_count"] == 2

    # Оба refresh-токена больше не валидны.
    r1 = await client.post(REFRESH_URL, json={"refresh_token": s1["refresh_token"]})
    assert r1.status_code == 401
    r2 = await client.post(REFRESH_URL, json={"refresh_token": s2["refresh_token"]})
    assert r2.status_code == 401


async def test_revoke_except_current_keeps_current_session(client, user_a):
    """except_current=true → текущая сессия остаётся, остальные revoked."""
    s1 = await _login_full(client, "t_user_a", "User1234!")
    s2 = await _login_full(client, "t_user_a", "User1234!")

    resp = await client.post(
        REVOKE_URL,
        headers={"Authorization": f"Bearer {s2['access_token']}"},
        json={"except_current": True},
    )
    assert resp.status_code == 200, resp.text
    # Снесено только s1.
    assert resp.json()["revoked_count"] == 1

    # s2 — refresh всё ещё работает.
    r2 = await client.post(REFRESH_URL, json={"refresh_token": s2["refresh_token"]})
    assert r2.status_code == 200

    # s1 — refresh 401.
    r1 = await client.post(REFRESH_URL, json={"refresh_token": s1["refresh_token"]})
    assert r1.status_code == 401


async def test_revoke_does_not_affect_other_users(client, user_a, user_b):
    """user_a жмёт «logout-all» — сессии user_b не страдают."""
    a1 = await _login_full(client, "t_user_a", "User1234!")
    b1 = await _login_full(client, "t_user_b", "User1234!")

    resp = await client.post(
        REVOKE_URL,
        headers={"Authorization": f"Bearer {a1['access_token']}"},
        json={"except_current": False},
    )
    assert resp.status_code == 200

    # user_b всё ещё может refresh'нуться.
    r = await client.post(REFRESH_URL, json={"refresh_token": b1["refresh_token"]})
    assert r.status_code == 200


async def test_revoke_requires_auth(client):
    resp = await client.post(REVOKE_URL, json={"except_current": False})
    assert resp.status_code == 401


# ── revoke one session ────────────────────────────────────────────────────


async def test_revoke_one_session_targets_specific(client, user_a):
    """DELETE /me/sessions/{id} — снимает конкретную сессию, остальные живут."""
    s1 = await _login_full(client, "t_user_a", "User1234!")
    s2 = await _login_full(client, "t_user_a", "User1234!")

    # Узнаём session_id первой сессии через listing.
    listing = await client.get(
        LIST_URL, headers={"Authorization": f"Bearer {s2['access_token']}"}
    )
    items = listing.json()["items"]
    # Берём НЕ current — это s1.
    target = next(it for it in items if not it["is_current"])
    target_sid = target["session_id"]

    resp = await client.delete(
        f"{LIST_URL}/{target_sid}",
        headers={"Authorization": f"Bearer {s2['access_token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked_count"] == 1

    # s1 refresh — 401, s2 refresh — 200.
    r1 = await client.post(REFRESH_URL, json={"refresh_token": s1["refresh_token"]})
    assert r1.status_code == 401
    r2 = await client.post(REFRESH_URL, json={"refresh_token": s2["refresh_token"]})
    assert r2.status_code == 200


async def test_revoke_one_foreign_session_returns_404(client, user_a, user_b):
    """user_a пытается revoked'нуть session_id юзера b → 404 (без раскрытия
    «чужая vs не существует»)."""
    a1 = await _login_full(client, "t_user_a", "User1234!")
    b1 = await _login_full(client, "t_user_b", "User1234!")

    # Узнаём session_id юзера b через его собственный listing.
    b_list = await client.get(
        LIST_URL, headers={"Authorization": f"Bearer {b1['access_token']}"}
    )
    b_sid = b_list.json()["items"][0]["session_id"]

    resp = await client.delete(
        f"{LIST_URL}/{b_sid}",
        headers={"Authorization": f"Bearer {a1['access_token']}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SESSION_NOT_FOUND"

    # У b ничего не повредилось.
    r = await client.post(REFRESH_URL, json={"refresh_token": b1["refresh_token"]})
    assert r.status_code == 200


async def test_revoke_one_current_session_emits_was_current_true(
    client, user_a, capture_audit_payloads,
):
    """DELETE /me/sessions/{id}, где {id} = sid текущего access-токена,
    проходит успешно и пишет в audit `was_current=True`. Параллельная сессия
    остаётся живой — её refresh продолжает работать.
    """
    s1 = await _login_full(client, "t_user_a", "User1234!")
    s2 = await _login_full(client, "t_user_a", "User1234!")

    # Узнаём session_id, помеченный is_current=True для s2.
    listing = await client.get(
        LIST_URL, headers={"Authorization": f"Bearer {s2['access_token']}"}
    )
    items = listing.json()["items"]
    current = next(it for it in items if it["is_current"])
    current_sid = current["session_id"]

    resp = await client.delete(
        f"{LIST_URL}/{current_sid}",
        headers={"Authorization": f"Bearer {s2['access_token']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked_count"] == 1

    # Свой refresh умер, второй сессии (s1) — жив.
    r2 = await client.post(REFRESH_URL, json={"refresh_token": s2["refresh_token"]})
    assert r2.status_code == 401
    r1 = await client.post(REFRESH_URL, json={"refresh_token": s1["refresh_token"]})
    assert r1.status_code == 200

    # Audit-event пишет was_current=True.
    events = [
        p for p in capture_audit_payloads
        if p.get("action") == "user.session_revoked_one"
        and p.get("details", {}).get("session_id") == current_sid
    ]
    assert events, "expected user.session_revoked_one audit event"
    assert events[0]["details"]["was_current"] is True
