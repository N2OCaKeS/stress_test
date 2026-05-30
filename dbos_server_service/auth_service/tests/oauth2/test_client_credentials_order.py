"""Порядок проверок в `client_credentials_token` + транзакционная рамка lockout-helper'а.

#1: grant_types-deny должен срабатывать ДО verify_secret — клиент без
`client_credentials` не платит за Argon2-hash и не двигает counter
`failed_secret_attempts` (а значит и не получит lockout, если кто-то
ошибочно дёргает m2m-endpoint на authorization_code-only клиенте).

#2: один db.commit() на failure-path, а не два sequential (release_expired
+ register_failure). При raise состояние счётчика остаётся в БД.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from src.models.oauth_client import OAuthClient

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
TOKEN_URL = "/api/auth/v1/oauth2/token"


async def _create_client(client, admin_token, dept_id, name, grant_types):
    return (await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "department_id": dept_id,
            "name": name,
            "grant_types": grant_types,
            "redirect_uris": ["https://app.example.com/cb"],
            "allowed_scopes": [],
        },
    )).json()


async def test_grant_types_deny_before_secret_verify(client, admin_token, dept_a, db):
    """Клиент без `client_credentials` в grant_types + любой (даже неверный)
    secret → 403 GRANT_TYPE_NOT_ALLOWED, а не 401 OAUTH_CLIENT_INVALID.

    Это доказывает: проверка grant_types fail-fast до verify_secret. Если бы
    verify шёл первым, неверный secret уходил бы в 401 и крутил счётчик
    `failed_secret_attempts` — лишняя CPU-нагрузка + ложный lockout по m2m-эндпоинту
    на клиенте, который вообще не должен туда ходить.
    """
    cc = await _create_client(
        client, admin_token, dept_a.id,
        name="grant_order_app",
        grant_types=["authorization_code"],
    )

    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": cc["client_id"],
        "client_secret": "wrong_secret",
    })
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "GRANT_TYPE_NOT_ALLOWED"

    # Счётчик не двинут — verify не успел сработать.
    row = await db.scalar(
        select(OAuthClient).where(OAuthClient.client_id == cc["client_id"])
    )
    await db.refresh(row)
    assert row.failed_secret_attempts == 0
    assert row.locked_until is None


async def test_failure_persists_with_single_commit(client, admin_token, dept_a, db):
    """Failure-path lockout-helper'а: одиночный commit перед raise. Счётчик
    должен оказаться в БД (а не откатиться вместе с raise через get_db()).
    """
    cc = await _create_client(
        client, admin_token, dept_a.id,
        name="single_commit_fail",
        grant_types=["client_credentials"],
    )

    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": cc["client_id"],
        "client_secret": "wrong_secret",
    })
    assert resp.status_code == 401, resp.text
    assert resp.json()["error_code"] == "OAUTH_CLIENT_INVALID"

    row = await db.scalar(
        select(OAuthClient).where(OAuthClient.client_id == cc["client_id"])
    )
    await db.refresh(row)
    assert row.failed_secret_attempts == 1
    assert row.locked_until is None


async def test_release_expired_and_failure_persist_together(
    client, admin_token, dept_a, db,
):
    """JIT-release устаревшего lockout'а + регистрация свежей неудачи проходят
    одним commit'ом. После raise в БД: counter=1 (от свежей неудачи),
    locked_until=NULL (release устаревшего блока).
    """
    cc = await _create_client(
        client, admin_token, dept_a.id,
        name="release_plus_fail",
        grant_types=["client_credentials"],
    )

    # Симулируем: 5 неудач + истёкший lockout.
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.execute(
        update(OAuthClient)
        .where(OAuthClient.client_id == cc["client_id"])
        .values(failed_secret_attempts=5, locked_until=past)
    )
    await db.commit()

    # Новый запрос с неверным secret: helper сначала release_if_expired
    # (counter=0, locked_until=None), затем verify провалится → register_failure
    # выставит counter=1. Всё фиксируется одним commit'ом перед raise.
    resp = await client.post(TOKEN_URL, json={
        "grant_type": "client_credentials",
        "client_id": cc["client_id"],
        "client_secret": "wrong_secret",
    })
    assert resp.status_code == 401, resp.text

    row = await db.scalar(
        select(OAuthClient).where(OAuthClient.client_id == cc["client_id"])
    )
    await db.refresh(row)
    assert row.failed_secret_attempts == 1
    assert row.locked_until is None
