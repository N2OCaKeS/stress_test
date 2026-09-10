"""`is_service_bot` в ответе `POST /authorization/introspect`.

False для user/обычного бота, True только для бота, заведённого bootstrap-кодом
(`bootstrap_worker_bot`/`bootstrap_testing_service_bot`) — единственного пути,
который может выставить `BotAccount.is_service_bot=True`.
"""

from datetime import timedelta

from src.utils.time import utcnow

INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
BOTS_URL = "/api/auth/v1/bots"


async def test_user_jwt_is_service_bot_false(client, user_a_token):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["subject_type"] == "user"
    assert body["is_service_bot"] is False


async def test_regular_bot_is_service_bot_false(client, admin_token, dept_a):
    created = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "ordinary_bot", "department_id": dept_a.id, "allowed_services": []},
    )
    assert created.status_code == 201, created.text
    bot_id = created.json()["bot_id"]

    tok = await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "tok", "expires_at": (utcnow() + timedelta(days=1)).isoformat()},
    )
    assert tok.status_code == 201, tok.text
    raw = tok.json()["token"]

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["subject_type"] == "bot"
    assert body["is_service_bot"] is False


async def test_bootstrap_service_bot_is_service_bot_true(db, monkeypatch):
    """Бот, заведённый `bootstrap_worker_bot`, отдаёт `is_service_bot=True`
    через introspect (вызов сервисного слоя напрямую, симметрично
    `test_bootstrap_platform.py`)."""
    from src.core import config as config_mod
    from src.services import authorization_service, bootstrap_service

    config_mod.get_settings.cache_clear()
    monkeypatch.setattr(bootstrap_service.audit_service, "emit", lambda *a, **k: None)
    token = "dbos_bot_" + "h" * 43
    monkeypatch.setenv("WORKER_BOT_TOKEN", token)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)
    await bootstrap_service.bootstrap_worker_bot(db)

    res = await authorization_service.introspect(db, token)
    assert res.active is True
    assert res.subject_type == "bot"
    assert res.is_service_bot is True

    config_mod.get_settings.cache_clear()
