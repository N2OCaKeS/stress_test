"""exchange_code: успешный verify_secret + reset_failed_attempts фиксирует counter
до raise на pre-CAS-ошибке (invalid code).

Раньше reset шёл без явного commit, поэтому ANY exception между reset и финальным
db.commit() (например, code не найден / expired / PKCE-mismatch) откатывал reset
через rollback в get_db(). Зеркало `client_credentials_token`, где commit стоит
сразу после reset.
"""

from sqlalchemy import select, update

from src.models.oauth_client import OAuthClient


CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"


async def _create_client(client, admin_token, dept_id):
    return (await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": "exch_reset_app",
            "grant_types": ["authorization_code"],
            "redirect_uris": ["https://app.example.com/cb"],
            "allowed_scopes": [],
        },
    )).json()


async def test_reset_persists_when_code_lookup_fails(client, admin_token, dept_a, db):
    cc = await _create_client(client, admin_token, dept_a.id)
    client_id = cc["client_id"]
    client_secret = cc["client_secret"]

    # Симулируем накопленные failed_secret_attempts.
    await db.execute(
        update(OAuthClient)
        .where(OAuthClient.client_id == client_id)
        .values(failed_secret_attempts=3, locked_until=None)
    )
    await db.commit()

    # Правильный secret → verify пройдёт, reset обнулит counter. Code битый —
    # после reset пойдёт raise OAUTH_CODE_INVALID. До фикса counter откатывался
    # вместе с rollback в get_db().
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": "completely-fake-authorization-code",
        "redirect_uri": "https://app.example.com/cb",
    })
    assert resp.status_code == 401, resp.text
    assert resp.json()["error_code"] == "OAUTH_CODE_INVALID"

    row = await db.scalar(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    await db.refresh(row)
    assert row.failed_secret_attempts == 0, (
        f"reset не закоммитился до raise — counter откатился, got {row.failed_secret_attempts}"
    )
