"""Тесты: POST /api/auth/v1/refresh — refresh берётся из cookie, если нет в body.

Body имеет приоритет (старые клиенты), отсутствие обоих → 422 MISSING_REFRESH_TOKEN.
"""

from tests._helpers.http import login as _login

URL = "/api/auth/v1/refresh"


async def test_refresh_uses_cookie_when_body_empty(client, account_admin):
    """После login httpx сохраняет cookie в client.cookies. POST без body
    должен отработать за счёт cookie."""
    await _login(client)
    resp = await client.post(URL, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body


async def test_refresh_uses_cookie_when_body_null(client, account_admin):
    """`{"refresh_token": null}` тоже должен схватить cookie."""
    await _login(client)
    resp = await client.post(URL, json={"refresh_token": None})
    assert resp.status_code == 200


async def test_refresh_body_takes_precedence_over_cookie(client, account_admin):
    """Логинимся дважды, ротируем s1 через body — cookie от s2 не должен мешать."""
    s1 = await _login(client)
    # После второго login cookie перезапишется на refresh из s2.
    s2 = await _login(client)
    assert client.cookies.get("dbos_refresh") == s2["refresh_token"]

    # Передаём в body refresh от s1 — он должен ротироваться, а не s2.
    resp = await client.post(URL, json={"refresh_token": s1["refresh_token"]})
    assert resp.status_code == 200

    # s1 теперь невалиден (его ротировали).
    resp_s1_again = await client.post(URL, json={"refresh_token": s1["refresh_token"]})
    assert resp_s1_again.status_code == 401


async def test_refresh_sets_new_cookie(client, account_admin):
    """После ротации в Set-Cookie должен прилететь новый refresh."""
    data = await _login(client)
    resp = await client.post(URL, json={"refresh_token": data["refresh_token"]})
    assert resp.status_code == 200
    raw_set_cookie = resp.headers.get("set-cookie", "")
    assert "dbos_refresh=" in raw_set_cookie
    assert "HttpOnly" in raw_set_cookie
    # Новый refresh в cookie должен совпасть с тем, что лежит в body.
    new_refresh = resp.json()["refresh_token"]
    assert resp.cookies.get("dbos_refresh") == new_refresh


async def test_refresh_missing_token_returns_422(client, account_admin):
    """Пустой body + нет cookie → 422 MISSING_REFRESH_TOKEN."""
    # Гарантируем, что cookie не лежит в jar клиента.
    client.cookies.clear()
    resp = await client.post(URL, json={})
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "MISSING_REFRESH_TOKEN"


async def test_refresh_no_body_uses_cookie(client, account_admin):
    """POST вообще без тела (ни json, ни content) должен схватить cookie.

    Раньше отсутствие тела ловилось FastAPI как `body: Field required` 422
    ещё до хендлера, и cookie-флоу был недостижим. Body теперь опционален.
    """
    await _login(client)
    resp = await client.post(URL)
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_refresh_no_body_no_cookie_returns_domain_422(client, account_admin):
    """Без тела и без cookie — доходим до хендлера и получаем доменный
    MISSING_REFRESH_TOKEN, а не сырой FastAPI `Field required`."""
    client.cookies.clear()
    resp = await client.post(URL)
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "MISSING_REFRESH_TOKEN"
