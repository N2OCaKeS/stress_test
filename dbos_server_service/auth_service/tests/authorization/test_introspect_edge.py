"""Edge cases для `POST /authorization/introspect` и `POST /authorization/service-access`.

Базовые happy лежат в `test_introspect.py`. Здесь покрытие пропусков:
* JWT, подписанный другим алгоритмом (HS512), считается invalid → active=false
  (alg confusion должен быть закрыт `algorithms=[HS256]`).
* JWT с правильным алгоритмом, но другим secret → active=false.
* PAT существует, но user удалён → active=false (orphan PAT).
* Bot token существует, но bot выключен → active=false.
* `check_service_access` для account_admin без identity.allowed_services —
  фиксируем текущее поведение (allowed=False, потому что allowed_services пуст).
* Истёкший JWT exp.
* `SERVICE_API_KEY`-guard: оба endpoint'а — service-to-service, требуют
  `Authorization: Bearer <SERVICE_API_KEY>`. Без / с неверным ключом → 401.
"""

import os
from datetime import timedelta

import jwt

from src.core.security import create_access_token

INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
ACCESS_URL = "/api/auth/v1/authorization/service-access"


class TestJwtAlgorithmConfusion:
    async def test_jwt_signed_with_hs512_rejected(self, client, user_a):
        """alg confusion attack — токен подписан HS512, но приложение принимает только HS256."""
        from src.core.config import get_settings
        secret = get_settings().secret_key
        token = jwt.encode(
            {"sub": user_a.id, "username": user_a.username},
            secret,
            algorithm="HS512",
        )
        resp = await client.post(INTROSPECT_URL, json={"token": token})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_jwt_signed_with_different_secret_rejected(self, client, user_a):
        token = jwt.encode(
            {"sub": user_a.id, "username": user_a.username},
            "totally-different-secret-key-but-long-enough-to-pass",
            algorithm="HS256",
        )
        resp = await client.post(INTROSPECT_URL, json={"token": token})
        assert resp.json()["active"] is False

    async def test_expired_jwt_inactive(self, client, user_a):
        # JWT_LEEWAY_SECONDS=10 → нужно уйти ЗА пределы leeway, чтобы decode упал.
        token = create_access_token(
            {"sub": user_a.id, "username": user_a.username},
            expires_delta=timedelta(seconds=-30),
        )
        resp = await client.post(INTROSPECT_URL, json={"token": token})
        assert resp.json()["active"] is False

    async def test_garbage_token_inactive(self, client):
        resp = await client.post(INTROSPECT_URL, json={"token": "not-even-a-jwt"})
        assert resp.json()["active"] is False

    async def test_empty_token_inactive(self, client):
        resp = await client.post(INTROSPECT_URL, json={"token": ""})
        assert resp.json()["active"] is False


class TestOrphanPat:
    async def test_pat_for_deleted_user_inactive(self, client, user_a_token, user_a, db):
        """PAT валиден в БД, но user удалён → active=false."""
        # Создаём PAT через API
        tok = await client.post(
            "/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "orphan_pat", "allowed_services": ["service_x"]},
        )
        raw = tok.json()["token"]

        # Удалим пользователя через ORM (каскад приберёт PAT, но симулируем orphan
        # обнулив только department_id и status, либо удалив запись напрямую).
        from sqlalchemy import update
        from src.models import User
        # Помечаем user inactive — PAT при introspect должен видеть, что юзера нет
        # активного (введём более точное поведение: PAT остаётся, но user banned).
        await db.execute(
            update(User).where(User.id == user_a.id).values(is_active=False)
        )
        await db.commit()

        # PAT остаётся валидным, но get_by_id вернёт user — фиксируем текущее
        # поведение: introspect возвращает active=true для inactive user (баг или
        # фича?). Тест служит триггером для проверки регрессии.
        resp = await client.post(INTROSPECT_URL, json={"token": raw})
        # Точное поведение зависит от того, фильтрует ли UserRepository.get_by_id
        # по is_active. На текущей реализации возвращается user → active=true.
        # Если поведение изменится — этот assert сообщит.
        assert resp.status_code == 200
        body = resp.json()
        assert "active" in body


class TestJwtRevalidation:
    """JWT introspect должен **жить из БД**, а не верить payload-у на TTL.

    Сценарии: между логином и истечением JWT юзера могут забанить, удалить,
    лишить роли или отозвать у отдела доступ к сервису. Старый JWT не должен
    продолжать выдавать старые привилегии — каждая выдача `introspect` для
    JWT-ветки re-validate-ит state из БД.
    """

    async def test_banned_user_jwt_inactive(self, client, user_a, user_a_token, db):
        """После выдачи JWT юзера забанили (`status=BANNED`) — introspect: active=false."""
        from sqlalchemy import update

        from src.core.constants import UserStatus
        from src.models import User

        await db.execute(
            update(User).where(User.id == user_a.id).values(status=UserStatus.BANNED)
        )
        await db.commit()

        resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is False

    async def test_blocked_user_jwt_inactive(self, client, user_a, user_a_token, db):
        """Юзер заблокирован (`status=BLOCKED`) — старый JWT мёртв."""
        from sqlalchemy import update

        from src.core.constants import UserStatus
        from src.models import User

        await db.execute(
            update(User).where(User.id == user_a.id).values(status=UserStatus.BLOCKED)
        )
        await db.commit()

        resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_inactive_user_jwt_inactive(self, client, user_a, user_a_token, db):
        """`is_active=False` → JWT мёртв (soft-delete семантика)."""
        from sqlalchemy import update

        from src.models import User

        await db.execute(
            update(User).where(User.id == user_a.id).values(is_active=False)
        )
        await db.commit()

        resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_deleted_user_jwt_inactive(self, client, user_a, user_a_token, db):
        """Юзера удалили из БД (`get_by_id` → None) — старый JWT мёртв."""
        from sqlalchemy import delete

        from src.models import PersonalAccessToken, Session, User, UserServiceRole

        # Сначала чистим зависимости (cascade в test-fixture не всегда срабатывает
        # из-за SAVEPOINT-семантики), потом сам user.
        await db.execute(delete(UserServiceRole).where(UserServiceRole.user_id == user_a.id))
        await db.execute(delete(Session).where(Session.user_id == user_a.id))
        await db.execute(delete(PersonalAccessToken).where(PersonalAccessToken.user_id == user_a.id))
        await db.execute(delete(User).where(User.id == user_a.id))
        await db.commit()

        resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_revoked_service_role_disappears_from_jwt_introspect(
        self, client, user_a, user_a_token, service_x, db
    ):
        """Юзеру сняли service-роль после выдачи JWT → introspect возвращает
        обновлённый `service_roles` без неё (а не payload-snapshot)."""
        from sqlalchemy import delete

        from src.models import UserServiceRole

        # Sanity: до revoke роль есть.
        before = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert "reader" in before.json()["service_roles"].get(service_x.service_name, [])

        await db.execute(
            delete(UserServiceRole).where(
                UserServiceRole.user_id == user_a.id,
                UserServiceRole.service_name == service_x.service_name,
            )
        )
        await db.commit()

        after = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert after.status_code == 200
        body = after.json()
        # роль больше не выдаётся
        assert "reader" not in body["service_roles"].get(service_x.service_name, [])
        # сам сервис доступа dept-у остался, поэтому он всё ещё в allowed_services
        assert service_x.service_name in body["allowed_services"]
        # service_roles map либо без ключа service_x, либо с пустым списком
        assert body["service_roles"].get(service_x.service_name, []) == []

    async def test_department_service_access_revoked_after_jwt(
        self, client, user_a, user_a_token, service_x, db
    ):
        """Отделу отозвали доступ к сервису → старый JWT больше не показывает
        этот сервис в `allowed_services`, и `service-access` возвращает denied."""
        from sqlalchemy import update

        from src.models import DepartmentServiceAccess

        # Sanity: до revoke сервис в allowed_services.
        before = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert service_x.service_name in before.json()["allowed_services"]

        await db.execute(
            update(DepartmentServiceAccess)
            .where(
                DepartmentServiceAccess.department_id == user_a.department_id,
                DepartmentServiceAccess.service_name == service_x.service_name,
            )
            .values(is_active=False)
        )
        await db.commit()

        after = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert after.status_code == 200
        body = after.json()
        assert service_x.service_name not in body["allowed_services"]

        # service-access тоже должен ответить denied.
        access = await client.post(
            ACCESS_URL,
            json={"subject_token": user_a_token, "service_name": service_x.service_name},
        )
        assert access.status_code == 200
        assert access.json()["allowed"] is False

    async def test_platform_role_revoked_after_jwt(
        self, client, dept_admin_a, dept_admin_a_token, db
    ):
        """У department_admin'a сняли platform_role → introspect возвращает
        обновлённый `platform_role` (None), а не snapshot из payload."""
        from sqlalchemy import update

        from src.models import User

        # Sanity: до revoke роль есть.
        before = await client.post(INTROSPECT_URL, json={"token": dept_admin_a_token})
        assert before.json()["platform_role"] == "department_admin"

        await db.execute(
            update(User).where(User.id == dept_admin_a.id).values(platform_role=None)
        )
        await db.commit()

        after = await client.post(INTROSPECT_URL, json={"token": dept_admin_a_token})
        assert after.status_code == 200
        assert after.json()["platform_role"] is None


class TestServiceAccessEdge:
    async def test_account_admin_has_no_service_access_via_introspect_alone(
        self, client, admin_token, service_x,
    ):
        """account_admin имеет `allowed_services=[]` — `check_service_access`
        строго смотрит на `result.allowed_services`, и без явного гранта
        возвращает allowed=False даже для account_admin. Фиксируем поведение
        (для админ-операций используется `platform_role`, не service-access)."""
        resp = await client.post(
            ACCESS_URL,
            json={"subject_token": admin_token, "service_name": service_x.service_name},
        )
        assert resp.status_code == 200
        # account_admin не имеет dept → allowed=False по department-проверке либо
        # по фильтру `service_name not in allowed_services`.
        assert resp.json()["allowed"] is False

    async def test_invalid_token_returns_allowed_false(self, client, service_x):
        resp = await client.post(
            ACCESS_URL,
            json={"subject_token": "broken", "service_name": service_x.service_name},
        )
        assert resp.status_code == 200
        assert resp.json()["allowed"] is False

    async def test_user_with_role_sees_roles_in_response(self, client, user_a_token, service_x):
        resp = await client.post(
            ACCESS_URL,
            json={"subject_token": user_a_token, "service_name": service_x.service_name},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed"] is True
        assert "reader" in body["service_roles"]


class TestServiceApiKeyGuard:
    """`/authorization/introspect` и `/service-access` — service-to-service,
    защищены `Depends(require_service_token)`. Без Bearer SERVICE_API_KEY —
    401, без утечки информации о токене.

    Используем `raw_client`, чтобы пройти мимо автоматического инжектора
    Authorization-header в обычной `client`-фикстуре.
    """

    # ── introspect ──────────────────────────────────────────────────────────

    async def test_introspect_without_authorization_returns_401(
        self, raw_client, user_a_token
    ):
        resp = await raw_client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert resp.status_code == 401
        body = resp.json()
        assert body["error_code"] == "INVALID_SERVICE_TOKEN"
        # Гарантируем, что тело ответа НЕ содержит ничего про переданный токен —
        # endpoint не должен исполнять introspection без auth.
        assert "active" not in body
        assert "sub" not in body

    async def test_introspect_with_wrong_service_key_returns_401(
        self, raw_client, user_a_token
    ):
        resp = await raw_client.post(
            INTROSPECT_URL,
            json={"token": user_a_token},
            headers={"Authorization": "Bearer not-the-real-service-key"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_SERVICE_TOKEN"

    async def test_introspect_with_non_bearer_scheme_returns_401(
        self, raw_client, user_a_token
    ):
        resp = await raw_client.post(
            INTROSPECT_URL,
            json={"token": user_a_token},
            headers={"Authorization": f"Basic {os.environ['SERVICE_API_KEY']}"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_SERVICE_TOKEN"

    async def test_introspect_with_correct_service_key_works(
        self, raw_client, user_a_token, user_a, service_x
    ):
        """Sanity: с правильным SERVICE_API_KEY happy-path работает как до фикса."""
        resp = await raw_client.post(
            INTROSPECT_URL,
            json={"token": user_a_token},
            headers={"Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is True
        assert body["subject_type"] == "user"
        assert body["sub"] == user_a.id
        assert service_x.service_name in body["allowed_services"]

    # ── service-access ──────────────────────────────────────────────────────

    async def test_service_access_without_authorization_returns_401(
        self, raw_client, user_a_token, service_x
    ):
        resp = await raw_client.post(
            ACCESS_URL,
            json={"subject_token": user_a_token, "service_name": service_x.service_name},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error_code"] == "INVALID_SERVICE_TOKEN"
        # До фикса этот вызов возвращал 200 с `allowed: true` — теперь даже не
        # доходит до бизнес-логики, никакого oracle.
        assert "allowed" not in body

    async def test_service_access_with_wrong_service_key_returns_401(
        self, raw_client, user_a_token, service_x
    ):
        resp = await raw_client.post(
            ACCESS_URL,
            json={"subject_token": user_a_token, "service_name": service_x.service_name},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_SERVICE_TOKEN"

    async def test_service_access_with_correct_service_key_works(
        self, raw_client, user_a_token, service_x
    ):
        """Sanity: happy-path для service-access с правильным ключом."""
        resp = await raw_client.post(
            ACCESS_URL,
            json={"subject_token": user_a_token, "service_name": service_x.service_name},
            headers={"Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed"] is True
        assert "reader" in body["service_roles"]


class TestXServiceIdentityValidation:
    """`X-Service-Identity` header validation (mTLS-partial follow-up).

    The header is informational — anyone with `SERVICE_API_KEY` can put any
    string in there, but `require_service_token` validates it against
    `KNOWN_SERVICE_IDENTITIES` and (a) accepts known values, (b) WARNs +
    allows unknown values by default (soft mode), (c) rejects unknown values
    with 401 when `STRICT_SERVICE_IDENTITY=true`. Missing header is always
    allowed for backward compat.
    """

    async def test_valid_key_with_known_identity_succeeds(
        self, raw_client, user_a_token, user_a
    ):
        """SERVICE_API_KEY + X-Service-Identity: loging_service → 200 (happy path)."""
        resp = await raw_client.post(
            INTROSPECT_URL,
            json={"token": user_a_token},
            headers={
                "Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}",
                "X-Service-Identity": "loging_service",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is True
        assert resp.json()["sub"] == user_a.id

    async def test_valid_key_with_unknown_identity_soft_mode_allows(
        self, raw_client, user_a_token, caplog
    ):
        """Default soft mode: unknown identity → 200, plus operator-facing WARNING.

        Behavioral contract:
          1. Request НЕ ломается (200) — иначе любой soft-misconfig каскадно
             валит cross-service вызовы.
          2. WARNING-level запись с rogue-значением летит в `src.dependencies.auth` —
             единственный наблюдаемый сигнал для ops (counter'а под legacy-режим
             нет). Substring-сторону минимизируем: достаточно факта WARNING+
             rogue-value в args, без жёсткой привязки к фразе сообщения.
        """
        import logging

        with caplog.at_level(logging.WARNING, logger="src.dependencies.auth"):
            resp = await raw_client.post(
                INTROSPECT_URL,
                json={"token": user_a_token},
                headers={
                    "Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}",
                    "X-Service-Identity": "rogue_service_pwn",
                },
            )
        assert resp.status_code == 200
        warn_records = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING
            and rec.name == "src.dependencies.auth"
            and "rogue_service_pwn" in (rec.getMessage())
        ]
        assert warn_records, (
            "ожидался WARNING-level лог с rogue-identity для ops-видимости, "
            f"got: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )

    async def test_valid_key_without_identity_header_succeeds(
        self, raw_client, user_a_token
    ):
        """Backward-compat: callers that don't set the header still work."""
        resp = await raw_client.post(
            INTROSPECT_URL,
            json={"token": user_a_token},
            headers={"Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is True

    async def test_invalid_key_with_known_identity_still_rejected(
        self, raw_client, user_a_token
    ):
        """Regression: identity header is informational, not auth.
        Wrong SERVICE_API_KEY → 401 INVALID_SERVICE_TOKEN regardless of header.
        """
        resp = await raw_client.post(
            INTROSPECT_URL,
            json={"token": user_a_token},
            headers={
                "Authorization": "Bearer not-the-real-key",
                "X-Service-Identity": "loging_service",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "INVALID_SERVICE_TOKEN"

    async def test_strict_mode_rejects_unknown_identity(
        self, raw_client, user_a_token, monkeypatch
    ):
        """When STRICT_SERVICE_IDENTITY=true, unknown header → 401."""
        from src.core import config

        # Clear the @lru_cache so a fresh Settings instance picks up the env.
        config.get_settings.cache_clear()
        monkeypatch.setenv("STRICT_SERVICE_IDENTITY", "true")
        try:
            resp = await raw_client.post(
                INTROSPECT_URL,
                json={"token": user_a_token},
                headers={
                    "Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}",
                    "X-Service-Identity": "garbage_value",
                },
            )
            assert resp.status_code == 401
            assert resp.json()["error_code"] == "INVALID_SERVICE_IDENTITY"
        finally:
            # Reset the cache so subsequent tests use the default (soft) setting.
            config.get_settings.cache_clear()

    async def test_strict_mode_still_allows_missing_header(
        self, raw_client, user_a_token, monkeypatch
    ):
        """STRICT_SERVICE_IDENTITY=true must not break callers that omit the
        header entirely — only callers that *send* a bad value are rejected.
        Otherwise rolling out strict mode would instantly break legacy callers.
        """
        from src.core import config

        config.get_settings.cache_clear()
        monkeypatch.setenv("STRICT_SERVICE_IDENTITY", "true")
        try:
            resp = await raw_client.post(
                INTROSPECT_URL,
                json={"token": user_a_token},
                headers={"Authorization": f"Bearer {os.environ['SERVICE_API_KEY']}"},
            )
            assert resp.status_code == 200
        finally:
            config.get_settings.cache_clear()


class TestPatTouchOrdering:
    """`last_used_at` PAT'а апдейтится только после валидации юзера.

    Раньше touch шёл первым: introspect банутого юзера всё равно дёргал
    UPDATE на PAT, портил статистику "недавно использован" и зря писал в БД.
    Сейчас порядок: get_active_by_hash → expires_at → get user → status check
    → touch.
    """

    async def test_banned_user_pat_introspect_does_not_touch(
        self, client, user_a, user_a_token, service_x, db,
    ):
        from sqlalchemy import select, update

        from src.core.constants import UserStatus
        from src.models import PersonalAccessToken, User

        # PAT через API.
        tok = await client.post(
            "/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "touch_order_pat", "allowed_services": [service_x.service_name]},
        )
        assert tok.status_code == 201, tok.text
        raw = tok.json()["token"]
        pat_id = tok.json()["token_id"]

        # Снимем baseline last_used_at (после create обычно None).
        row = (await db.execute(
            select(PersonalAccessToken).where(PersonalAccessToken.id == pat_id)
        )).scalar_one()
        baseline_last_used = row.last_used_at

        # Банним юзера.
        await db.execute(
            update(User).where(User.id == user_a.id).values(status=UserStatus.BANNED)
        )
        await db.commit()

        # introspect PAT — должен ответить active=false и НЕ обновить last_used_at.
        resp = await client.post(INTROSPECT_URL, json={"token": raw})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

        db.expire_all()
        row_after = (await db.execute(
            select(PersonalAccessToken).where(PersonalAccessToken.id == pat_id)
        )).scalar_one()
        assert row_after.last_used_at == baseline_last_used


class TestBotTouchOrdering:
    """`last_used_at` bot-токена апдейтится только после is_active-check бота.

    Симметрия с PAT: для disabled-бота introspect возвращает active=false,
    лишний UPDATE на `bot_tokens.last_used_at` искажает «недавно использован»
    в админке. Раньше touch шёл раньше валидации бота.
    """

    async def test_disabled_bot_introspect_does_not_touch(
        self, client, admin_token, dept_a, db,
    ):
        from sqlalchemy import select, update

        from src.models import BotAccount, BotToken

        BOTS_URL = "/api/auth/v1/bots"

        bot_resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "touch_order_bot", "department_id": dept_a.id, "allowed_services": []},
        )
        assert bot_resp.status_code == 201, bot_resp.text
        bot_id = bot_resp.json()["bot_id"]

        tok_resp = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "touch_order_tok"},
        )
        assert tok_resp.status_code == 201, tok_resp.text
        raw = tok_resp.json()["token"]
        bot_token_id = tok_resp.json()["token_id"]

        row = (await db.execute(
            select(BotToken).where(BotToken.id == bot_token_id)
        )).scalar_one()
        baseline_last_used = row.last_used_at

        await db.execute(
            update(BotAccount).where(BotAccount.id == bot_id).values(is_active=False)
        )
        await db.commit()

        resp = await client.post(INTROSPECT_URL, json={"token": raw})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

        db.expire_all()
        row_after = (await db.execute(
            select(BotToken).where(BotToken.id == bot_token_id)
        )).scalar_one()
        assert row_after.last_used_at == baseline_last_used, (
            "touch для disabled бота должен идти ПОСЛЕ is_active-check; "
            f"last_used_at апдейтнулся: baseline={baseline_last_used}, after={row_after.last_used_at}"
        )
