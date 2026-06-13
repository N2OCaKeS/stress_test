"""exchange_code: ошибка PKCE-verifier'а НЕ сжигает authorization code.

`mark_used` в `exchange_code` идёт ПОСЛЕ PKCE-проверки и ДО неё commit'а нет,
поэтому raise на неверном/отсутствующем verifier'е откатывается через get_db()
и `used_at` остаётся NULL — тот же код можно обменять заново с верным verifier'ом.

Это закрепляет инвариант, на который опирается комментарий в `exchange_code`
(consume после валидаций): pre-consume ошибки retryable, успех — one-shot.
Парный one-shot-на-успехе сценарий покрыт в `test_code_replay_race.py`.
"""

import base64
import hashlib

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"
TOKEN_URL = "/api/auth/v1/oauth2/token"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def _create_authcode_client(http_client, admin_token, dept_id, *, name):
    resp = await http_client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/callback"],
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _authorize_and_get_code(http_client, user_token, oauth_client, *, challenge, method):
    auth_resp = await http_client.get(
        AUTHORIZE_URL,
        headers={"Authorization": f"Bearer {user_token}"},
        params={
            "client_id": oauth_client["client_id"],
            "redirect_uri": "https://app.example.com/callback",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": method,
        },
        follow_redirects=False,
    )
    assert auth_resp.status_code == 302, auth_resp.text
    return auth_resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]


def _token_body(oauth_client, code, *, code_verifier=None):
    body = {
        "grant_type": "authorization_code",
        "client_id": oauth_client["client_id"],
        "client_secret": oauth_client["client_secret"],
        "code": code,
        "redirect_uri": "https://app.example.com/callback",
    }
    if code_verifier is not None:
        body["code_verifier"] = code_verifier
    return body


async def test_wrong_verifier_does_not_burn_code_then_correct_succeeds(
    client, admin_token, user_a_token, dept_a,
):
    """Неверный verifier → 401, но код не потрачен; повтор с верным → 200,
    после успеха код one-shot — третий обмен → 401."""
    verifier = "retry-after-bad-verifier-1234567890abcdef-correct-one"
    challenge = _s256(verifier)

    oauth_client = await _create_authcode_client(
        client, admin_token, dept_a.id, name="verifier_retry_app",
    )
    code = await _authorize_and_get_code(
        client, user_a_token, oauth_client, challenge=challenge, method="S256",
    )

    # Неверный verifier — PKCE не проходит, mark_used не вызывается.
    bad = await client.post(
        TOKEN_URL,
        json=_token_body(oauth_client, code, code_verifier="totally-wrong-verifier-zzz"),
    )
    assert bad.status_code == 401, bad.text
    assert bad.json()["error_code"] == "INVALID_GRANT"

    # Тот же код, теперь верный verifier — код не сожжён, обмен проходит.
    good = await client.post(
        TOKEN_URL,
        json=_token_body(oauth_client, code, code_verifier=verifier),
    )
    assert good.status_code == 200, (
        "ошибка verifier'а не должна сжигать код: повтор с верным verifier'ом "
        f"ожидался 200, got: {good.status_code} {good.text}"
    )
    assert "access_token" in good.json()

    # А вот теперь код consume'нут — one-shot, третий обмен отбивается.
    again = await client.post(
        TOKEN_URL,
        json=_token_body(oauth_client, code, code_verifier=verifier),
    )
    assert again.status_code == 401, again.text
    assert again.json()["error_code"] == "OAUTH_CODE_INVALID"
    assert "access_token" not in again.json()


async def test_missing_verifier_does_not_burn_code_then_correct_succeeds(
    client, admin_token, user_a_token, dept_a,
):
    """Отсутствующий verifier (код выписан с challenge) → 401, код не потрачен;
    повтор с верным verifier'ом → 200."""
    verifier = "missing-then-supplied-verifier-abcdef-1234567890-test"
    challenge = _s256(verifier)

    oauth_client = await _create_authcode_client(
        client, admin_token, dept_a.id, name="verifier_missing_retry_app",
    )
    code = await _authorize_and_get_code(
        client, user_a_token, oauth_client, challenge=challenge, method="S256",
    )

    # verifier не передан вовсе — PKCE-ветка рейзит до consume.
    missing = await client.post(TOKEN_URL, json=_token_body(oauth_client, code))
    assert missing.status_code == 401, missing.text
    assert missing.json()["error_code"] == "INVALID_GRANT"

    # Код цел, обмен с верным verifier'ом проходит.
    good = await client.post(
        TOKEN_URL,
        json=_token_body(oauth_client, code, code_verifier=verifier),
    )
    assert good.status_code == 200, good.text
    assert "access_token" in good.json()
