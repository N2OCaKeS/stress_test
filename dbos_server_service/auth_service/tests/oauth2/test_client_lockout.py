"""Lockout per-client для OAuth `client_credentials` brute-force.

Закрывает SEC-auth P1-1: счётчик `failed_secret_attempts` + `locked_until`
на `OAuthClient`. Симметрия с user-lockout (5 промахов → 15 минут).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"


async def _create_cc_client(client, admin_token, dept_id, name):
    return (await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": ["client_credentials"],
            "redirect_uris": [],
            "allowed_scopes": [],
        },
    )).json()


async def test_lockout_triggers_after_max_attempts(client, admin_token, dept_a):
    """5 промахов подряд → 6-й запрос даёт 429 ACCOUNT_TEMPORARILY_LOCKED."""
    cc = await _create_cc_client(client, admin_token, dept_a.id, name="lockout_basic")

    for _ in range(5):
        resp = await client.post(TOKEN_URL, json={
            "grant_type": "client_credentials",
            "client_id": cc["client_id"],
            "client_secret": "wrong_secret",
        })
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "OAUTH_CLIENT_INVALID"

    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": cc["client_id"],
        "client_secret": cc["client_secret"],
    })
    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"
    assert body["details"]["retry_after_seconds"] >= 0


async def test_lockout_blocks_correct_secret_too(client, admin_token, dept_a):
    """Залоченный клиент с правильным secret тоже получает 429."""
    cc = await _create_cc_client(client, admin_token, dept_a.id, name="lockout_correct")

    for _ in range(5):
        await client.post(TOKEN_URL, json={
            "grant_type": "client_credentials",
            "client_id": cc["client_id"],
            "client_secret": "wrong_secret",
        })

    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": cc["client_id"],
        "client_secret": cc["client_secret"],
    })
    assert resp.status_code == 429, resp.text


async def test_successful_auth_resets_counter(client, admin_token, dept_a, db):
    """Успешный verify сбрасывает счётчик в БД до нуля."""
    cc = await _create_cc_client(client, admin_token, dept_a.id, name="lockout_reset")

    for _ in range(4):
        await client.post(TOKEN_URL, json={
            "grant_type": "client_credentials",
            "client_id": cc["client_id"],
            "client_secret": "wrong_secret",
        })

    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": cc["client_id"],
        "client_secret": cc["client_secret"],
    })
    assert resp.status_code == 200, resp.text

    # Проверяем напрямую в БД: счётчик сброшен.
    from sqlalchemy import select
    from src.models.oauth_client import OAuthClient
    row = await db.scalar(
        select(OAuthClient).where(OAuthClient.client_id == cc["client_id"])
    )
    await db.refresh(row)
    assert row.failed_secret_attempts == 0
    assert row.locked_until is None


async def test_expired_lockout_released_jit(client, admin_token, dept_a, db):
    """`locked_until` в прошлом → JIT-сброс на следующем запросе → 401, не 429."""
    cc = await _create_cc_client(client, admin_token, dept_a.id, name="lockout_expired")

    for _ in range(5):
        await client.post(TOKEN_URL, json={
            "grant_type": "client_credentials",
            "client_id": cc["client_id"],
            "client_secret": "wrong_secret",
        })

    # Сдвигаем locked_until в прошлое.
    from src.models.oauth_client import OAuthClient
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.execute(
        update(OAuthClient)
        .where(OAuthClient.client_id == cc["client_id"])
        .values(locked_until=past)
    )
    await db.commit()

    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": cc["client_id"],
        "client_secret": cc["client_secret"],
    })
    assert resp.status_code == 200, resp.text


async def test_lockout_applies_to_authorization_code_exchange(
    client, admin_token, user_a_token, dept_a,
):
    """Lockout сработавший на client_credentials также блокирует
    `/token` exchange кода (тот же `client_secret`-pipeline)."""
    cc = (await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_a.id,
            "name": "lockout_mix",
            "grant_types": ["authorization_code", "client_credentials"],
            "redirect_uris": ["https://app.example.com/cb"],
            "allowed_scopes": [],
        },
    )).json()

    # Выписываем code до lockout'а.
    auth_resp = await client.get(
        "/api/auth/v1/oauth2/authorize",
        headers={"Authorization": f"Bearer {user_a_token}"},
        params={
            "client_id": cc["client_id"],
            "redirect_uri": "https://app.example.com/cb",
            "response_type": "code",
        },
        follow_redirects=False,
    )
    code = auth_resp.headers["location"].split("code=", 1)[1].split("&", 1)[0]

    for _ in range(5):
        await client.post(TOKEN_URL, json={
            "grant_type": "client_credentials",
            "client_id": cc["client_id"],
            "client_secret": "wrong_secret",
        })

    # exchange кода тем же secret — должен попасть в lockout.
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "authorization_code",
        "client_id": cc["client_id"],
        "client_secret": cc["client_secret"],
        "code": code,
        "redirect_uri": "https://app.example.com/cb",
    })
    assert resp.status_code == 429, resp.text
    assert resp.json()["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"
