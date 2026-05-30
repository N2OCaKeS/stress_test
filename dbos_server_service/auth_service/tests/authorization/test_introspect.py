"""Тесты: POST /api/auth/v1/authorization/introspect и /service-access — проверка токенов и доступа к сервисам."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from src.core.security import create_access_token, hash_opaque_token
from src.models import PersonalAccessToken

INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
ACCESS_URL = "/api/auth/v1/authorization/service-access"
LOGIN_URL = "/api/auth/v1/login"
TOKENS_URL = "/api/auth/v1/tokens"
BOTS_URL = "/api/auth/v1/bots"


# ── JWT introspect ────────────────────────────────────────────────────────────

async def test_valid_jwt_is_active(client, user_a_token):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["subject_type"] == "user"


async def test_invalid_token_is_inactive(client):
    resp = await client.post(INTROSPECT_URL, json={"token": "garbage.token.value"})
    assert resp.json()["active"] is False


async def test_jwt_contains_correct_sub(client, user_a, user_a_token):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert resp.json()["sub"] == user_a.id


async def test_jwt_contains_allowed_services(client, user_a_token, service_x):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert service_x.service_name in resp.json()["allowed_services"]


async def test_account_admin_jwt_has_no_services(client, admin_token):
    resp = await client.post(INTROSPECT_URL, json={"token": admin_token})
    assert resp.json()["allowed_services"] == []


async def test_jwt_returns_username(client, user_a, user_a_token):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert resp.json()["username"] == user_a.username


async def test_jwt_returns_platform_role_for_admin(client, admin_token):
    resp = await client.post(INTROSPECT_URL, json={"token": admin_token})
    assert resp.json()["platform_role"] == "account_admin"


async def test_jwt_returns_platform_role_null_for_user(client, user_a_token):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert resp.json()["platform_role"] is None


async def test_jwt_returns_is_banned_false_by_default(client, user_a_token):
    resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
    assert resp.json()["is_banned"] is False


async def test_expired_jwt_is_inactive(client, user_a):
    """Валидно подписанный JWT с exp в прошлом → active=false.

    Дельта берётся с запасом > `JWT_LEEWAY_SECONDS` (default 10s), иначе
    decode пускает токен как ещё-не-протухший в пределах clock-skew.
    """
    expired = create_access_token(
        {"sub": user_a.id, "username": user_a.username},
        expires_delta=timedelta(minutes=-5),
    )
    resp = await client.post(INTROSPECT_URL, json={"token": expired})
    assert resp.status_code == 200
    assert resp.json()["active"] is False


# ── PAT introspect ────────────────────────────────────────────────────────────

async def test_pat_is_active(client, user_a_token):
    raw = (await client.post(TOKENS_URL, headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"name": "intr_pat", "allowed_services": ["service_x"]})).json()["token"]
    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.json()["active"] is True
    assert resp.json()["subject_type"] == "user"


async def test_pat_returns_username_and_platform_role(client, user_a, user_a_token):
    raw = (await client.post(TOKENS_URL, headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"name": "intr_pat_with_fields", "allowed_services": ["service_x"]})).json()["token"]
    body = (await client.post(INTROSPECT_URL, json={"token": raw})).json()
    assert body["username"] == user_a.username
    assert body["platform_role"] is None
    assert body["is_banned"] is False


async def test_expired_pat_is_inactive(client, user_a_token, db):
    """PAT с expires_at в прошлом → active=false."""
    raw = (await client.post(TOKENS_URL, headers={"Authorization": f"Bearer {user_a_token}"},
                              json={"name": "expired_pat", "allowed_services": ["service_x"]})).json()["token"]

    await db.execute(
        update(PersonalAccessToken)
        .where(PersonalAccessToken.token_hash == hash_opaque_token(raw))
        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=10))
    )
    await db.commit()

    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.status_code == 200
    assert resp.json()["active"] is False


# ── Bot token introspect ──────────────────────────────────────────────────────

async def test_bot_token_is_active(client, admin_token, dept_a):
    bot_id = (await client.post(BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"},
                                 json={"name": "intr_bot2", "department_id": dept_a.id,
                                       "allowed_services": []})).json()["bot_id"]
    raw = (await client.post(f"{BOTS_URL}/{bot_id}/tokens",
                              headers={"Authorization": f"Bearer {admin_token}"},
                              json={"name": "it"})).json()["token"]
    resp = await client.post(INTROSPECT_URL, json={"token": raw})
    assert resp.json()["active"] is True
    assert resp.json()["subject_type"] == "bot"


# ── Service access ────────────────────────────────────────────────────────────

async def test_service_access_allowed(client, user_a_token, service_x):
    resp = await client.post(ACCESS_URL, json={"subject_token": user_a_token, "service_name": service_x.service_name})
    assert resp.status_code == 200
    assert resp.json()["allowed"] is True


async def test_service_access_denied_wrong_service(client, user_a_token):
    resp = await client.post(ACCESS_URL, json={"subject_token": user_a_token, "service_name": "nonexistent_svc"})
    assert resp.json()["allowed"] is False


async def test_service_access_denied_invalid_token(client, service_x):
    resp = await client.post(ACCESS_URL, json={"subject_token": "bad_token", "service_name": service_x.service_name})
    assert resp.json()["allowed"] is False


async def test_service_access_dept_b_no_access_to_svc_x(client, user_b_token, service_x):
    """user_b is in dept_b which has no access to service_x."""
    resp = await client.post(ACCESS_URL, json={"subject_token": user_b_token, "service_name": service_x.service_name})
    assert resp.json()["allowed"] is False


async def test_introspect_bot_token_forwards_caller_ip_to_tracker(
    client, admin_token, dept_a, monkeypatch,
):
    """HTTP `/introspect` с `caller_ip` пробрасывает его в `track_bot_ip`.

    Регрессия: до фикса IntrospectRequest.caller_ip терялся между endpoint'ом
    и authorization_service.introspect → multi-IP детектор всегда видел None.
    """
    captured: list[dict] = []
    import src.services.bot_ip_tracker as tracker_mod
    original = tracker_mod.track_bot_ip

    async def _spy(db, bot, caller_ip, request_id=None):
        captured.append({"bot_id": bot.id, "caller_ip": caller_ip})
        return await original(db, bot, caller_ip, request_id=request_id)

    # `authorization_service.introspect` делает lazy `from ... import track_bot_ip`
    # внутри bot-ветки, поэтому подмена должна жить в самом модуле tracker'а.
    monkeypatch.setattr(tracker_mod, "track_bot_ip", _spy)

    bot_id = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "caller_ip_bot", "department_id": dept_a.id, "allowed_services": []},
    )).json()["bot_id"]
    raw = (await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "cip_tok"},
    )).json()["token"]

    resp = await client.post(
        INTROSPECT_URL,
        json={"token": raw, "caller_ip": "203.0.113.7"},
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is True

    # tracker должен быть позван ровно один раз с тем IP, что прислали.
    bot_calls = [c for c in captured if c["bot_id"] == bot_id]
    assert len(bot_calls) == 1, bot_calls
    assert bot_calls[0]["caller_ip"] == "203.0.113.7"


async def test_service_access_includes_roles(client, user_a_token, service_x):
    resp = await client.post(ACCESS_URL, json={"subject_token": user_a_token, "service_name": service_x.service_name})
    assert "reader" in resp.json()["service_roles"]


# ── audit status convention: всё, что не success, идёт как `failure` ──────────


async def test_service_access_denied_emits_failure_status(
    client, user_b_token, service_x, monkeypatch,
):
    """dept_b не подключён к service_x — audit-event с status=failure (не denied).

    Конвенция единая по auth_service: success / failure / error.
    `denied` исторически плавал по разным emit-сайтам и путал SIEM-классификацию.
    """
    from src.services import audit_service as audit_mod

    captured: list[dict] = []
    original = audit_mod.emit

    def _capture(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _capture)

    resp = await client.post(
        ACCESS_URL,
        json={"subject_token": user_b_token, "service_name": service_x.service_name},
    )
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False

    access = [e for e in captured if e["action"] == "service.access_check"]
    assert access, captured
    assert access[-1]["status"] == "failure"
    assert access[-1]["allowed"] is False
    assert access[-1]["details"].get("reason") == "department_no_access"


async def test_service_access_not_in_token_emits_failure_status(
    client, dept_a_with_service, service_x, admin_token, monkeypatch,
):
    """Department подключён, но конкретный сервис не в allowed_services токена.

    Сценарий искусственный: достаём токен у юзера, который не разрешён на
    сервис, через token без сервиса в allowed_services. Используем второй
    сервис из соседнего dept'а — проще проверить через nonexistent service
    в маршруте `service_not_in_token`.
    """
    from src.services import audit_service as audit_mod

    captured: list[dict] = []
    original = audit_mod.emit

    def _capture(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _capture)

    # Создаём бот без allowed_services вообще — тогда `service_not_in_token`
    # отработает на любой подключённый к департаменту сервис.
    bot_id = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "no_svc_bot", "department_id": dept_a_with_service.id,
              "allowed_services": []},
    )).json()["bot_id"]
    raw = (await client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "tok"},
    )).json()["token"]

    captured.clear()
    resp = await client.post(
        ACCESS_URL,
        json={"subject_token": raw, "service_name": service_x.service_name},
    )
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False

    access = [e for e in captured if e["action"] == "service.access_check"]
    assert access, captured
    assert access[-1]["status"] == "failure"
    assert access[-1]["details"].get("reason") == "service_not_in_token"
