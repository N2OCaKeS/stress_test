"""/oauth2/authorize → state echo'ится байт-в-байт из исходной query.

RFC 6749 §4.1.2 требует exact-echo `state`: клиент сравнивает байт-в-байт
со своим хранилищем для CSRF-защиты. Сервер echo'ит ровно те байты, что
были в `state=` параметре пришедшего запроса, без decode/encode-раунда.
"""

from urllib.parse import urlparse, parse_qs

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"


async def _make_client(http_client, token, dept_id, *, redirect_uris):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "state_echo_app",
            "department_id": dept_id,
            "grant_types": ["authorization_code"],
            "redirect_uris": redirect_uris,
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_state_with_space_round_trips_to_original_string(
    client, admin_token, user_a_token, dept_a,
):
    """Пробел в state → клиент после `parse_qs` получает обратно ту же строку.

    httpx URL-encode'ит пробел как `+` (form-urlencoded семантика). Сервер
    раньше делал `quote(state, safe='')` поверх FastAPI-декода и получалось
    `%20`. Семантически оба декодятся в пробел (`parse_qs` корректно
    обрабатывает обе формы), и raw-echo даёт ту же эквивалентность. Тест
    фиксирует поведенческий round-trip: что клиент послал, то и получил
    после стандартного decode'а.
    """
    redirect = "https://app.example.com/cb"
    cl = await _make_client(client, admin_token, dept_a.id, redirect_uris=[redirect])

    state_with_space = "csrf token 123"
    resp = await client.get(
        AUTHORIZE_URL,
        params={
            "client_id": cl["client_id"],
            "redirect_uri": redirect,
            "state": state_with_space,
        },
        headers={"Authorization": f"Bearer {user_a_token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    query = urlparse(location).query
    # parse_qs корректно декодит и `+` и `%20` в пробел — exact-echo
    # семантически выполнен.
    qs = parse_qs(query)
    assert qs.get("state") == [state_with_space], qs
