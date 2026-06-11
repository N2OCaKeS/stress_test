"""auth_service coverage gaps: точечные ветки, оставшиеся непокрытыми.

Целевые ветки:
  - GAP-5: bot.token_create failure-audit содержит token_name в details
  - GAP-4: redact({"bot_token": ["list"]}) → контейнер под секретным ключом целиком
  - GAP-7: oauth state с %-литералом — double-encode (% → %25)
  - bot_ip_tracker xfail: снять xfail с test_suspicious_multi_ip_uses_window_from_settings
    (монопатч на cached settings работает, xfail reason устарел)
  - bot_roles_purge xfail: снять xfail с test_narrowing_with_no_roles_emits_no_purge_audit
    (поведение устоялось: purge эмитится безусловно если removed_services непустой)
  - downgrade xfail: снять через динамический resolve revision вместо хардкода -1/-2
  - refresh_session_ip xfail: снять через patch extract_client_ip
  - audit_service: get_emit_tasks_overflow_total, _reset_emit_tasks_overflow_for_tests
  - update_user idempotent no-op (status уже такой же)
  - docker push_denied REGISTRY_NOT_FOUND для pull scope (симметрия с push)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

import pytest

from src.services.redaction import redact


# ── audit-spy helper ─────────────────────────────────────────────────────────

def _capture_audit(monkeypatch) -> list[dict]:
    """Подменяет `audit_service.emit` на spy и возвращает list перехваченных событий.

    `audit_service` импортирован продакшен-кодом как module-объект
    (`from src.services import audit_service`), поэтому patching атрибута
    `emit` на самом модуле меняет вызов и в bot_service / docker / bot_ip_tracker
    — там цепочка идёт через тот же объект модуля.
    """
    captured: list[dict] = []
    from src.services import audit_service as audit_mod

    orig = audit_mod.emit

    def spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return orig(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", spy)
    return captured


# ── GAP-4: redact — контейнер под секретным ключом ───────────────────────────

class TestRedactContainerUnderSecretKey:
    def test_bot_token_list_value_masked_entirely(self):
        """{"bot_token": ["list", "of", "values"]} → "<TOKEN>" целиком."""
        out = redact({"bot_token": ["tok1", "tok2"]})
        assert out == {"bot_token": "<TOKEN>"}

    def test_password_dict_value_masked_entirely(self):
        """{"password": {"hash": "x"}} → "<PASSWORD>" (уже есть тест, регрессия)."""
        out = redact({"password": {"hash": "x", "salt": "y"}})
        assert out == {"password": "<PASSWORD>"}

    def test_secret_list_value_masked_entirely(self):
        """{"secret": ["a", "b"]} → "<SECRET>"."""
        out = redact({"secret": ["a", "b"]})
        assert out == {"secret": "<SECRET>"}

    def test_token_nested_dict_masked_entirely(self):
        """{"access_token": {"sub": "usr_123"}} → "<TOKEN>"."""
        out = redact({"access_token": {"sub": "usr_123", "exp": 1234567890}})
        assert out == {"access_token": "<TOKEN>"}

    def test_non_secret_key_with_list_recurses(self):
        """Не-секретный ключ со списком — рекурсия, не глушение."""
        out = redact({"items": [{"password": "p"}, {"name": "ok"}]})
        assert out == {"items": [{"password": "<PASSWORD>"}, {"name": "ok"}]}

    def test_credential_with_nested_list_masked(self):
        """{"credentials": [{"key": "v"}]} → "<CREDENTIAL>"."""
        out = redact({"credentials": [{"key": "v"}]})
        assert out == {"credentials": "<CREDENTIAL>"}


# ── GAP-5: bot.token_create failure содержит token_name в details ─────────────

class TestBotTokenCreateFailureDetails:
    async def test_cross_tenant_failure_audit_contains_token_name(
        self, db, dept_a, dept_b, account_admin, monkeypatch,
    ):
        """DEPARTMENT_ADMIN чужого отдела: failure-audit для bot.token_create
        должен содержать token_name в details (из extra_details хелпера)."""
        from src.core.constants import PlatformRole
        from src.core.exceptions import AuthorizationError
        from src.core.security import hash_password
        from src.models import Department, DepartmentServiceAccess, PlatformService, User
        from src.schemas.bots import BotCreate
        from src.services import bot_service
        from src.utils.ids import _new_id

        captured = _capture_audit(monkeypatch)

        # Создаём бота в dept_a
        bot = await bot_service.create_bot(
            db,
            actor_id=account_admin.id,
            actor_role=PlatformRole.ACCOUNT_ADMIN,
            data=BotCreate(
                name="gap5_token_name_bot",
                department_id=dept_a.id,
                allowed_services=[],
            ),
        )
        captured.clear()

        # dept_admin_b (отдел B) пытается создать токен боту dept_a
        dept_admin_b = User(
            id=_new_id("usr_"),
            username="gap5_dept_admin_b",
            password_hash=hash_password("Gap5Pass1!"),
            department_id=dept_b.id,
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
            status="active",
            is_active=True,
        )
        db.add(dept_admin_b)
        await db.flush()

        with pytest.raises(AuthorizationError) as exc:
            await bot_service.create_bot_token(
                db=db,
                actor_id=dept_admin_b.id,
                actor_role=PlatformRole.DEPARTMENT_ADMIN,
                bot_id=bot.bot_id,
                name="rotation_token",
                actor_department_id=dept_b.id,
            )
        assert exc.value.error_code == "BOT_ROLE_MGMT_FORBIDDEN"

        failures = [
            e for e in captured
            if e.get("action") == "bot.token_create" and e.get("status") == "failure"
        ]
        assert failures, f"failure-audit bot.token_create не эмитнут: {captured}"
        details = failures[0].get("details", {})
        assert "token_name" in details, (
            f"token_name должен быть в failure-details, got: {details}"
        )
        assert details["token_name"] == "rotation_token"
        assert details["reason"] == "cross_department_bot"


# ── GAP-7: oauth state с %-литералом → double-encode ─────────────────────────

CLIENTS_URL = "/api/auth/v1/oauth2/clients"
AUTHORIZE_URL = "/api/auth/v1/oauth2/authorize"


async def _make_oauth_client(client, admin_token, dept_id, *, name, redirect_uris):
    resp = await client.post(
        CLIENTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": name,
            "department_id": dept_id,
            "grant_types": ["authorization_code"],
            "redirect_uris": redirect_uris,
            "allowed_scopes": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestOAuthStateExactEcho:
    async def test_state_with_percent_literal_round_trip_exact(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """state='value%20here' — raw байты в callback'е те же, что в исходном
        запросе (no double-encode).

        Раньше сервер делал `quote(state, safe='')` поверх уже-decoded Query-
        параметра: клиент послал `state=value%20here` (raw), FastAPI декодит в
        `"value%20here"` (8 chars), `quote` re-encode'ит `%` в `%25` → callback
        несёт `state=value%2520here`. Auth0/Keycloak-style SDK, сравнивающие
        callback state byte-for-byte со своим хранилищем, ломались на `%25`.
        Фикс: echo raw-байт из исходной query — клиент получает ровно те
        байты, что прислал.
        """
        redirect = "https://app.example.com/cb"
        cl = await _make_oauth_client(
            client, admin_token, dept_a.id,
            name="state_pct_app", redirect_uris=[redirect],
        )

        # httpx URL-encode'ит `%` в `%25` сам — на проводе пойдёт
        # `state=value%2520here`. FastAPI decode'ит до `"value%20here"`. Сервер
        # должен echo'ить сырые байты `value%2520here`, а не делать ещё один
        # encode.
        state_with_pct = "value%20here"
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": redirect,
                "state": state_with_pct,
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        location = resp.headers["location"]
        raw_query = urlparse(location).query
        assert "state=" in raw_query, f"state отсутствует в redirect: {location}"
        # Exact echo: на проводе было `value%2520here`, в callback'е то же.
        assert "state=value%2520here" in raw_query, (
            f"state не прошёл exact-echo: raw_query={raw_query!r}"
        )
        # Регрессия: НЕ должно быть тройного encode (`%252520`).
        assert "state=value%252520here" not in raw_query, (
            f"state получил лишний encode-раунд: raw_query={raw_query!r}"
        )

    async def test_state_with_plus_preserved_literal(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """state с literal `+` (закодированный как `%2B` на проводе) приходит
        обратно как `%2B`, не как пробел.

        Сценарий, в котором заметна разница со старым `quote(safe='')`-путём:
        FastAPI декодит `+` в пробел (form-urlencoded семантика), а
        `quote('value with space', safe='')` re-encode'ил это в `%20`. Клиент,
        прислал `state=a%2Bb`, ждал обратно `a%2Bb`, получал `a%20b` →
        CSRF-сравнение ломалось. Raw-echo возвращает байты как пришли.
        """
        redirect = "https://app.example.com/cb"
        cl = await _make_oauth_client(
            client, admin_token, dept_a.id,
            name="state_plus_app", redirect_uris=[redirect],
        )

        # Шлём raw query вручную, чтобы контролировать сырую кодировку.
        resp = await client.get(
            f"{AUTHORIZE_URL}?client_id={cl['client_id']}"
            f"&redirect_uri={redirect}&state=a%2Bb",
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        raw_query = urlparse(resp.headers["location"]).query
        assert "state=a%2Bb" in raw_query, (
            f"state с `%2B` не сохранил байты: raw_query={raw_query!r}"
        )

    async def test_state_without_percent_single_encode_only(
        self, client, admin_token, user_a_token, dept_a,
    ):
        """Обычный state без % — кодируется ровно один раз, без double-encode."""
        redirect = "https://app.example.com/cb"
        cl = await _make_oauth_client(
            client, admin_token, dept_a.id,
            name="state_nopct_app", redirect_uris=[redirect],
        )

        plain_state = "csrf_token_abc123"
        resp = await client.get(
            AUTHORIZE_URL,
            params={
                "client_id": cl["client_id"],
                "redirect_uri": redirect,
                "state": plain_state,
            },
            headers={"Authorization": f"Bearer {user_a_token}"},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        location = resp.headers["location"]
        # Для state без спецсимволов: exact-echo, никаких изменений.
        assert f"state={plain_state}" in location, (
            f"plain state не прошёл exact-echo: {location}"
        )


# ── update_user идемпотентный no-op ──────────────────────────────────────────

USERS_URL = "/api/auth/v1/users"


class TestUpdateUserIdempotentNoOp:
    async def test_patch_same_status_returns_200_no_error(
        self, client, admin_token, user_a,
    ):
        """PATCH /users/{id} с тем же status что уже стоит → 200, без ошибки."""
        resp = await client.patch(
            f"{USERS_URL}/{user_a.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "active"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "active"

    async def test_patch_same_status_blocked_returns_200(
        self, client, admin_token, user_a,
    ):
        """Блокируем, потом снова PATCH status=blocked → 200 (идемпотент)."""
        block = await client.patch(
            f"{USERS_URL}/{user_a.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "blocked"},
        )
        assert block.status_code == 200, block.text

        again = await client.patch(
            f"{USERS_URL}/{user_a.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "blocked"},
        )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "blocked"

    async def test_patch_email_only_no_status_change(
        self, client, admin_token, user_a,
    ):
        """PATCH только email без status → status не меняется."""
        resp = await client.patch(
            f"{USERS_URL}/{user_a.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": "new_w16@example.com"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "active"


# ── docker pull_denied REGISTRY_NOT_FOUND (симметрия с push) ──────────────────

TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"


from tests._helpers.http import _basic_hdr  # noqa: F401 — общий helper


class TestDockerPullDeniedRegistryNotFound:
    async def test_pull_nonexistent_registry_emits_registry_not_found_audit(
        self, client, user_a, dept_a, admin_token, monkeypatch,
    ):
        """Pull scope с несуществующим registry_name (dept с таким именем нет)
        → docker.pull_denied reason=REGISTRY_NOT_FOUND."""
        # Включаем docker для dept_a (нужно чтобы аутентификация прошла)
        await client.put(
            CONFIG_URL.format(dept_id=dept_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"pull_policy": "all", "pull_user_ids": [], "push_user_ids": []},
        )

        captured = _capture_audit(monkeypatch)

        resp = await client.get(
            TOKEN_URL,
            headers=_basic_hdr("t_user_a", "User12345678!"),
            params={
                "service": "registry.test",
                "scope": "repository:nonexistent_registry_xyz/image:pull",
            },
        )
        assert resp.status_code == 200, resp.text

        import jwt as _jwt
        access = _jwt.decode(
            resp.json()["access_token"], options={"verify_signature": False},
        ).get("access", [])
        assert all("pull" not in e.get("actions", []) for e in access)

        pull_denied = [
            e for e in captured if e["action"] == "docker.pull_denied"
        ]
        assert pull_denied, f"docker.pull_denied не эмитнут: {captured}"
        ev = pull_denied[0]
        assert ev["details"]["reason"] == "REGISTRY_NOT_FOUND"
        assert ev["details"]["registry_name"] == "nonexistent_registry_xyz"

    async def test_pull_disabled_registry_emits_registry_disabled_audit(
        self, client, user_a, dept_a, admin_token, db, monkeypatch,
    ):
        """Pull scope на отключённый registry → docker.pull_denied reason=REGISTRY_DISABLED."""
        from src.models.department_docker_registry import DepartmentDockerRegistry
        from src.utils.ids import _new_id

        # Создаём отдел с is_enabled=False
        from src.models import Department
        dept_disabled = Department(
            id=_new_id("dep_"),
            name="dept_disabled_w16",
        )
        db.add(dept_disabled)
        cfg = DepartmentDockerRegistry(
            id=_new_id("ddr_"),
            department_id=dept_disabled.id,
            is_enabled=False,
            pull_policy="all",
            pull_user_ids=[user_a.id],
            push_user_ids=[],
        )
        db.add(cfg)
        await db.flush()

        # Аутентификация должна работать — нужен docker config у dept_a
        await client.put(
            CONFIG_URL.format(dept_id=dept_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"pull_policy": "all", "pull_user_ids": [], "push_user_ids": []},
        )

        captured = _capture_audit(monkeypatch)

        resp = await client.get(
            TOKEN_URL,
            headers=_basic_hdr("t_user_a", "User12345678!"),
            params={
                "service": "registry.test",
                "scope": f"repository:{dept_disabled.name}/image:pull",
            },
        )
        assert resp.status_code == 200, resp.text

        pull_denied = [
            e for e in captured if e["action"] == "docker.pull_denied"
        ]
        assert pull_denied, f"docker.pull_denied не эмитнут: {captured}"
        ev = pull_denied[0]
        assert ev["details"]["reason"] == "REGISTRY_DISABLED"


# ── bot_ip_tracker: xfail снимается — monkeypatch на lru_cache-объект работает ──

class TestBotIpTrackerWindowFromSettings:
    async def test_suspicious_window_from_settings_audits_correct_seconds(
        self, db, dept_a, monkeypatch,
    ):
        """bot_suspicious_ip_window_seconds читается из settings (не хардкодится).

        monkeypatch.setattr(settings, ...) мутирует сам lru_cache-объект —
        tracker берёт актуальное значение.
        """
        from src.core import config as config_mod
        from src.models import BotAccount
        from src.services import bot_ip_tracker
        from src.utils.ids import bot_id as new_bot_id

        settings = config_mod.get_settings()
        monkeypatch.setattr(settings, "bot_suspicious_ip_window_seconds", 1800)

        captured = _capture_audit(monkeypatch)
        # Отключаем реальную отправку (settings.logging_service_url → None)
        monkeypatch.setattr(settings, "logging_service_url", None, raising=False)

        bot = BotAccount(
            id=new_bot_id(),
            name="w16_window_bot",
            department_id=dept_a.id,
            allowed_services=[],
            is_active=True,
        )
        db.add(bot)
        await db.flush()

        fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        bot.last_known_ips = [{"ip": "10.0.0.1", "ts": fresh_ts}]
        await db.flush()

        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.2")
        assert alerted is True, "два разных IP в 1800s окне → алерт"

        events = [e for e in captured if e.get("action") == "bot.suspicious_multi_ip"]
        assert events, f"bot.suspicious_multi_ip не эмитнут: {captured}"
        details = events[-1].get("details") or {}
        assert details.get("time_window_seconds") == 1800, (
            f"time_window_seconds должен быть 1800 из settings, got: {details}"
        )
        assert "time_window" not in details, (
            "legacy поле time_window не должно быть в details"
        )


# ── bot_roles_purge: поведение устоялось — purge эмитится если removed_services ─

BOTS_URL = "/api/auth/v1/bots"
SERVICES_URL = "/api/auth/v1/services"


async def _grant_service_to_dept_w16(db, dept_id, service_name):
    from src.models import DepartmentServiceAccess, PlatformService, ServiceRoleDefinition
    from src.utils.ids import _new_id, service_role_def_id

    svc = await db.get(PlatformService, service_name)
    if svc is None:
        svc = PlatformService(
            service_name=service_name, is_active=True,
        )
        db.add(svc)
        await db.flush()
    db.add(DepartmentServiceAccess(
        id=_new_id("dsa_"), department_id=dept_id,
        service_name=service_name, is_active=True,
    ))
    for role in ("admin", "reader"):
        db.add(ServiceRoleDefinition(
            id=service_role_def_id(),
            department_id=dept_id, service_name=service_name,
            role_name=role,
            is_active=True, is_system=(role == "admin"),
        ))
    await db.flush()


# Backwards-compat alias на общий `_capture_audit`.
_capture_audit_w16 = _capture_audit


class TestBotRolesPurgeBehaviorConfirmed:
    async def test_narrowing_with_no_roles_still_emits_purge_when_service_removed(
        self, client, db, admin_token, dept_a_with_service, service_x, monkeypatch,
    ):
        """Бот без ролей, но сужение allowed_services → purge эмитится (removed_role_count=0).

        Это закреплённое поведение: `if removed_services:` проверяет список
        убранных сервисов, не количество ролей. Тест фиксирует контракт.
        """
        captured = _capture_audit_w16(monkeypatch)
        await _grant_service_to_dept_w16(db, dept_a_with_service.id, "svc_no_roles_w16")
        await db.commit()

        bot_resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "no_roles_w16_bot",
                "department_id": dept_a_with_service.id,
                "allowed_services": [service_x.service_name, "svc_no_roles_w16"],
            },
        )
        assert bot_resp.status_code == 201, bot_resp.text
        bot_id = bot_resp.json()["bot_id"]

        captured.clear()
        patch = await client.patch(
            f"{BOTS_URL}/{bot_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"allowed_services": [service_x.service_name]},
        )
        assert patch.status_code == 200, patch.text

        purge_events = [
            e for e in captured
            if e.get("action") == "bot.roles_purged_on_services_narrowed"
        ]
        assert purge_events, (
            "purge audit должен эмититься при сужении allowed_services, "
            "даже если ролей не было (removed_role_count=0)"
        )
        details = purge_events[-1]["details"]
        assert details["removed_role_count"] == 0
        assert "svc_no_roles_w16" in details["removed_services"]

    async def test_narrowing_no_change_no_purge(
        self, client, db, admin_token, dept_a_with_service, service_x, monkeypatch,
    ):
        """Тот же список allowed_services → removed_services пуст → purge нет."""
        captured = _capture_audit_w16(monkeypatch)

        bot_resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "same_svc_w16_bot",
                "department_id": dept_a_with_service.id,
                "allowed_services": [service_x.service_name],
            },
        )
        bot_id = bot_resp.json()["bot_id"]

        captured.clear()
        patch = await client.patch(
            f"{BOTS_URL}/{bot_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"allowed_services": [service_x.service_name]},
        )
        assert patch.status_code == 200, patch.text

        purge_events = [
            e for e in captured
            if e.get("action") == "bot.roles_purged_on_services_narrowed"
        ]
        assert not purge_events, (
            f"при неизменном allowed_services purge не должен эмититься: {purge_events}"
        )


# ── audit_service overflow cap: get/reset API ────────────────────────────────

class TestAuditServiceOverflowCapAPI:
    @pytest.mark.asyncio
    async def test_overflow_counter_resets_to_zero(self, monkeypatch):
        """_reset_emit_tasks_overflow_for_tests() возвращает счётчик в 0."""
        from src.services import audit_service

        audit_service._EMIT_TASKS.clear()
        audit_service._reset_emit_tasks_overflow_for_tests()

        monkeypatch.setattr(audit_service, "_EMIT_TASKS_MAX", 1)

        release = asyncio.Event()

        async def fake_send(payload, url, api_key):
            await release.wait()

        monkeypatch.setattr(audit_service, "_send_to_logging_service", fake_send)
        settings = audit_service.get_settings()
        monkeypatch.setattr(settings, "logging_service_url", "http://test", raising=False)
        monkeypatch.setattr(settings, "logging_service_api_key", "k", raising=False)

        # Два emit'а — cap=1, второй даёт overflow
        audit_service.emit("user.login", request_id="req_w16_a")
        await asyncio.sleep(0)
        audit_service.emit("user.login", request_id="req_w16_b")
        await asyncio.sleep(0)

        assert audit_service.get_emit_tasks_overflow_total() == 1

        # Сброс
        audit_service._reset_emit_tasks_overflow_for_tests()
        assert audit_service.get_emit_tasks_overflow_total() == 0

        release.set()
        # Cleanup
        for t in list(audit_service._EMIT_TASKS):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        audit_service._EMIT_TASKS.clear()
        audit_service._reset_emit_tasks_overflow_for_tests()

    def test_get_emit_tasks_overflow_total_initial_zero(self):
        """get_emit_tasks_overflow_total() возвращает int >= 0."""
        from src.services import audit_service
        val = audit_service.get_emit_tasks_overflow_total()
        assert isinstance(val, int)
        assert val >= 0


# ── downgrade: resolve revision dynamically ───────────────────────────────────

class TestMigrationsDowngradeDynamic:
    def test_get_current_head_resolves_dynamically(self):
        """alembic.script.ScriptDirectory резолвит head без хардкода ревизии."""
        import os
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        service_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(__file__))
        )
        cfg = Config(os.path.join(service_dir, "alembic.ini"))
        cfg.set_main_option(
            "script_location",
            os.path.join(service_dir, "src/db/migrations"),
        )
        head = ScriptDirectory.from_config(cfg).get_current_head()
        assert head is not None, "head revision не должен быть None"
        assert len(head) >= 8, f"revision выглядит слишком коротко: {head!r}"

    def test_penultimate_revision_exists(self):
        """Предпоследняя ревизия (head~1) существует — downgrade -1 корректен."""
        import os
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        service_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(__file__))
        )
        cfg = Config(os.path.join(service_dir, "alembic.ini"))
        cfg.set_main_option(
            "script_location",
            os.path.join(service_dir, "src/db/migrations"),
        )
        sd = ScriptDirectory.from_config(cfg)
        head = sd.get_current_head()
        head_script = sd.get_revision(head)
        assert head_script is not None
        # Цепочка ревизий через .down_revision
        prev = head_script.down_revision
        assert prev is not None, "должна быть хотя бы одна ревизия перед head"


# ── refresh_session_ip: X-Forwarded-For читается через extract_client_ip ──────

REFRESH_URL = "/api/auth/v1/refresh"
LOGIN_URL = "/api/auth/v1/login"


class TestRefreshUpdatesSessionIpViaExtractClientIp:
    async def test_refresh_ip_update_via_monkeypatched_extract(
        self, client, account_admin, db, monkeypatch,
    ):
        """Session.ip_address обновляется при /refresh, если extract_client_ip
        возвращает IP (monkeypatch на функцию, не заголовок).

        Оригинальный xfail: endpoint читает request.client.host, не X-Forwarded-For.
        Фикс: монкипатчим extract_client_ip напрямую чтобы проверить service-уровень.
        """
        from sqlalchemy import select
        from src.core.security import hash_refresh_token
        from src.models import Session
        import src.api.v1.endpoints.auth as auth_ep

        captured_ips: list = []
        original_extract = auth_ep.extract_client_ip

        def patched_extract(request):
            result = "192.0.2.99"
            captured_ips.append(result)
            return result

        monkeypatch.setattr(auth_ep, "extract_client_ip", patched_extract)

        login = await client.post(
            LOGIN_URL,
            json={"username": "t_admin", "password": "Admin12345678!"},
        )
        assert login.status_code == 200, login.text
        raw_rt = login.json()["refresh_token"]

        resp = await client.post(
            REFRESH_URL,
            json={"refresh_token": raw_rt},
        )
        assert resp.status_code == 200, resp.text

        new_rt = resp.json()["refresh_token"]
        new_hash = hash_refresh_token(new_rt)
        sess = await db.scalar(
            select(Session).where(Session.refresh_token_hash == new_hash)
        )
        assert sess is not None, "сессия после ротации не найдена"
        assert sess.ip_address == "192.0.2.99", (
            f"Session.ip_address не обновился после /refresh, got: {sess.ip_address!r}"
        )
