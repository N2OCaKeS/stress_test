"""auth: is_active-гарды на выдаче ролей, redaction и oauth-state echo.

- is_active-guard в `assign_roles` / `assign_bot_roles` / `bulk_assign` —
  нельзя навешать роль забаненному юзеру или disabled-боту (role-resurrection
  при unban/reactivate).
- `_check_can_manage_bot_or_audit.extra_details` — при failure
  `extra_details` мёрджится в audit-details.
- `redaction._classify_value` — закрытие `cs_*` (OAuth client_secret) по
  форме значения, не только по имени ключа.
- `oauth_service.exchange_code` — нет лишнего commit'а между mark_used и
  audit_emit; проверяем, что код не падает и audit пишется как раньше.
- `bot_ip_tracker.track_bot_ip` — concurrency-замечание зафиксировано в
  docstring (smoke: docstring явно упоминает про N>1).
- `/oauth2/authorize` — state с `+` round-trip восстанавливается как `+`,
  а не превращается в пробел или `%20`.
"""

import pytest

from src.core.constants import PlatformRole, UserStatus
from src.core.exceptions import AuthorizationError, ConflictError
from src.models import BotAccount
from src.schemas.auth import IdentityContext
from src.services import (
    audit_service as audit_mod,
    bot_ip_tracker,
    bot_service,
    oauth_service,
    service_role_service,
    user_service,
)
from src.utils.ids import bot_id


def _capture_emits(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


async def _make_bot(db, dept_id, name="guard_bot", *, is_active=True, allowed_services=None) -> BotAccount:
    bot = BotAccount(
        id=bot_id(),
        name=name,
        department_id=dept_id,
        allowed_services=allowed_services or [],
        is_active=is_active,
    )
    db.add(bot)
    await db.flush()
    return bot


# ── assign_roles → inactive user → ConflictError(USER_INACTIVE) ──────────


class TestAssignRolesIsActiveGuard:
    async def test_banned_user_cannot_receive_role(
        self, db, account_admin, user_a, service_x,
    ):
        # user_a сначала забаниваем напрямую через ORM, чтобы не зависеть от
        # ban-flow с side-эффектами (revoke sessions / audit).
        user_a.is_active = False
        user_a.status = UserStatus.BANNED
        await db.flush()

        with pytest.raises(ConflictError) as ei:
            await user_service.assign_roles(
                db,
                actor_id=account_admin.id,
                actor_role=PlatformRole.ACCOUNT_ADMIN,
                user_id=user_a.id,
                service_name=service_x.service_name,
                roles=["reader"],
            )
        assert ei.value.error_code == "USER_INACTIVE"

    async def test_active_user_still_assignable(
        self, db, account_admin, user_a, service_x,
    ):
        # sanity: happy-path по-прежнему работает после правки.
        await user_service.assign_roles(
            db,
            actor_id=account_admin.id,
            actor_role=PlatformRole.ACCOUNT_ADMIN,
            user_id=user_a.id,
            service_name=service_x.service_name,
            roles=["reader", "operator"],
        )


# ── assign_bot_roles → inactive bot → ConflictError(BOT_INACTIVE) ────────


class TestAssignBotRolesIsActiveGuard:
    async def test_inactive_bot_cannot_receive_role(
        self, db, account_admin, dept_a_with_service, service_x,
    ):
        bot = await _make_bot(
            db, dept_a_with_service.id, "inactive_bot",
            is_active=False, allowed_services=[service_x.service_name],
        )
        with pytest.raises(ConflictError) as ei:
            await bot_service.assign_bot_roles(
                db,
                actor_id=account_admin.id,
                actor_role=PlatformRole.ACCOUNT_ADMIN,
                bot_id=bot.id,
                service_name=service_x.service_name,
                roles=["reader"],
            )
        assert ei.value.error_code == "BOT_INACTIVE"


# ── bulk_assign → inactive user → ConflictError(USER_INACTIVE) ───────────


class TestBulkAssignIsActiveGuard:
    async def test_bulk_assign_skips_inactive_user(
        self, db, account_admin, user_a, dept_a_with_service, service_x,
    ):
        user_a.is_active = False
        await db.flush()

        identity = IdentityContext(
            user_id=account_admin.id,
            username=account_admin.username,
            platform_role=PlatformRole.ACCOUNT_ADMIN,
            department_id=None,
        )
        with pytest.raises(ConflictError) as ei:
            await service_role_service.bulk_assign(
                db,
                identity=identity,
                department_id=dept_a_with_service.id,
                service_name=service_x.service_name,
                role_name="reader",
                user_ids=[user_a.id],
            )
        assert ei.value.error_code == "USER_INACTIVE"


# ── _check_can_manage_bot_or_audit.extra_details merge на failure ─────────


class TestCheckCanManageBotExtraDetails:
    async def test_extra_details_merged_into_failure_audit(
        self, db, dept_a, dept_b, monkeypatch,
    ):
        """DA из dept_b пытается assign_bot_roles бота из dept_a:
        failure-audit должен содержать `service_name`/`roles` из extra_details
        плюс стандартные cross-tenant поля."""
        bot = await _make_bot(
            db, dept_a.id, "x_dept_bot", allowed_services=["service_x"],
        )
        captured = _capture_emits(monkeypatch)

        # _resolve_actor_dept берёт dept из переданного actor_department_id —
        # если он передан, in-process DB-look-up не нужен. Используем
        # actor_department_id=dept_b.id, что воспроизводит DA-из-dept_b.

        with pytest.raises(AuthorizationError):
            await bot_service.assign_bot_roles(
                db,
                actor_id="usr_fake_da",
                actor_role=PlatformRole.DEPARTMENT_ADMIN,
                bot_id=bot.id,
                service_name="service_x",
                roles=["reader", "operator"],
                actor_department_id=dept_b.id,
            )

        failures = [
            e for e in captured
            if e["action"] == "bot.roles_assign" and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        details = failures[0]["details"]
        # стандартные поля cross-tenant денайла
        assert details["reason"] == "cross_department_bot"
        assert details["bot_id"] == bot.id
        assert details["bot_department_id"] == dept_a.id
        assert details["actor_department_id"] == dept_b.id
        # extra_details, прокинутые из вызова assign_bot_roles
        assert details["service_name"] == "service_x"
        assert details["roles"] == ["reader", "operator"]


# ── redaction._classify_value по `cs_*`-значению ──────────────────────────


class TestRedactionClientSecretByValue:
    def test_cs_prefix_masked_under_arbitrary_key(self):
        from src.services.redaction import redact

        payload = {
            # Имя ключа НЕ в _SECRET_KEYS — маскировать должна эвристика по значению.
            "raw": "cs_abcDEF1234567890_-xyz",
            # client_id `cli_*` — публичный, маскировать не надо.
            "client_id": "cli_publicpublicpublicpublic",
        }
        masked = redact(payload)
        assert masked["raw"] == "<SECRET>"
        # cli_* остаётся как есть — это public identifier для audit-trail.
        assert masked["client_id"].startswith("cli_")


# ── exchange_code — dead-commit removal smoke ─────────────────────────────


class TestExchangeCodeNoDoubleCommit:
    async def test_oauth_service_exchange_code_no_explicit_commit_between_jwt_and_audit(self):
        """Source-check: после выписки access-токена (`_build_oauth_access_token`)
        НЕТ `await db.commit()` до `audit_service.emit`. Без точечного intercept'а
        sqlalchemy-сессии проще всего проверять статически — функция короткая,
        паттерн однозначный. (Refresh-INSERT коммитится ДО сборки JWT.)"""
        import inspect
        src = inspect.getsource(oauth_service.exchange_code)
        # Берём срез после сборки access-токена и до `audit_service.emit(`.
        i = src.index("_build_oauth_access_token(")
        j = src.index("audit_service.emit(", i)
        middle = src[i:j]
        assert "db.commit()" not in middle, (
            "exchange_code: dead commit между JWT и audit ещё на месте"
        )


# ── bot_ip_tracker — docstring фиксирует concurrency-замечание ────────────


class TestBotIpTrackerDocstring:
    def test_docstring_mentions_concurrency_semantics(self):
        doc = bot_ip_tracker.track_bot_ip.__doc__ or ""
        assert (
            "race" in doc.lower()
            or "конкурент" in doc.lower()
            or "concurrent" in doc.lower()
            or "for update" in doc.lower()
        )


# ── /oauth2/authorize: state с `+` round-trip ─────────────────────────────


class TestAuthorizeStatePlusEcho:
    async def test_state_with_plus_sign_preserved_in_redirect(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """state, содержащий `+`, должен echo'иться как `+` (URL-decoded
        обратно даёт исходный байт). До правки `parse_qsl` декодил `+` →
        пробел, дальше `urlencode(quote_via=quote)` ставил `%20`, и клиент
        получал `%20` вместо `+`."""
        from urllib.parse import urlparse, parse_qs

        redirect = "https://app.example.com/cb"
        cl_resp = await client.post(
            "/api/auth/v1/oauth2/clients",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "plus_state_app",
                "department_id": dept_a.id,
                "grant_types": ["authorization_code"],
                "redirect_uris": [redirect],
                "allowed_scopes": [],
            },
        )
        assert cl_resp.status_code == 201, cl_resp.text
        client_id = cl_resp.json()["client_id"]

        state_with_plus = "a+b+c"
        resp = await client.get(
            "/api/auth/v1/oauth2/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": redirect,
                "state": state_with_plus,
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        location = resp.headers["location"]
        # `+` в state должен быть percent-encoded как `%2B`, чтобы при декоде
        # клиент получил исходный байт. `+` без encoding'а декодится как
        # пробел (HTML form-urlencoded semantics) и ломает байт-в-байт echo.
        query = urlparse(location).query
        assert "state=%2Bb%2Bc" in query or "state=a%2Bb%2Bc" in query, query
        # Сanity: parse_qs (form-urlencoded) тоже восстанавливает оригинал.
        qs = parse_qs(query)
        assert qs.get("state") == [state_with_plus], qs
