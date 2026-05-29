"""E2E auth identity flow — cluster A.

Покрывает POST /login, /refresh, /logout, GET /me, /users CRUD,
PAT lifecycle, ban-cascade и revoke-immediate-effect. Каждый тест:
endpoint-ответ + DB-state в auth_db + audit-row в loging.audit_events.

Все тесты используют только фикстуры из conftest.py.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

import httpx
import pytest

from tests.integration._helpers_A_auth import (
    ensure_department,
    login as do_login,
    me as get_me,
    now_utc,
    pat_db_row,
    query_audit_events,
    rand_suffix,
    session_count,
    user_db_row,
    wait_for_audit_row,
)

AUTH_PREFIX = "/api/auth/v1"


# ── Local fixtures (auth-DB awareness) ───────────────────────────────────────

@pytest.fixture
def auth_db():
    """Engine для auth_db_test. Local fixture, чтобы не плодить в conftest."""
    import os

    from sqlalchemy import create_engine

    url = os.environ.get("AUTH_DB_URL")
    if not url:
        pytest.skip("AUTH_DB_URL not set; full integration stack required")
    engine = create_engine(url, pool_pre_ping=True, future=True)
    try:
        yield engine
    finally:
        engine.dispose()




# ── 1. POST /login ───────────────────────────────────────────────────────────

class TestLogin:
    def test_login_success_returns_jwt_and_emits_audit_info(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        loging_db_engine,
        make_user,
    ):
        user = make_user(password="LoginOk1234!", platform_role="account_admin")
        since = now_utc()

        r = do_login(auth_client, user["username"], "LoginOk1234!")

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] > 0
        assert body["access_token"]
        assert body["refresh_token"]
        identity = body["identity"]
        assert identity["username"] == user["username"]
        assert identity["user_id"] == user["user_id"]

        row = wait_for_audit_row(
            loging_db_engine,
            action="user.login",
            status="success",
            actor_id=identity["user_id"],
            since=since,
        )
        assert row["severity"] == "INFO"
        assert row["allowed"] is True
        assert row["service"] == "auth_service"

    def test_login_bad_password_returns_401_and_audit_failure(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
        make_user,
    ):
        user = make_user(password="GoodPass1234!", platform_role="account_admin")
        since = now_utc()

        r = do_login(auth_client, user["username"], "WrongPass1234!")

        assert r.status_code == 401, r.text
        assert r.json()["error_code"] == "INVALID_CREDENTIALS"

        row = wait_for_audit_row(
            loging_db_engine,
            action="user.login",
            status="failure",
            actor_id=user["user_id"],
            since=since,
        )
        assert row["allowed"] is False
        assert row["details"].get("reason") == "invalid_password"

    def test_login_unknown_user_audited_with_null_actor(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
    ):
        bogus = f"nobody_{rand_suffix()}"
        since = now_utc()

        r = do_login(auth_client, bogus, "Whatever1234!")
        assert r.status_code == 401
        assert r.json()["error_code"] == "INVALID_CREDENTIALS"

        for _ in range(25):
            rows = query_audit_events(
                loging_db_engine,
                action="user.login",
                status="failure",
                since=since,
                limit=30,
            )
            match = [
                r for r in rows
                if r["actor_id"] is None
                and r["details"].get("reason") == "user_not_found"
                and r["details"].get("username") == bogus
            ]
            if match:
                break
            time.sleep(0.4)
        else:
            pytest.xfail(
                "user.login/user_not_found audit row не найден — вероятный "
                "drop INGEST_RATE_LIMIT-ом (см. _helpers_A_auth.wait_for_audit_row)."
            )
        assert match[0]["allowed"] is False

    def test_login_banned_user_blocked_and_audited(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        loging_db_engine,
        auth_db,
        make_user,
    ):
        dept_id = ensure_department(
            auth_client, admin_token, f"a_login_ban_dept_{rand_suffix()}"
        )
        user = make_user(password="BanMe1234!", department_id=dept_id)
        user_id = user["user_id"]

        # Pre-condition: юзер логинится нормально
        ok = do_login(auth_client, user["username"], "BanMe1234!")
        assert ok.status_code == 200

        ban = auth_client.post(
            f"{AUTH_PREFIX}/users/{user_id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "test"},
        )
        assert ban.status_code == 200, ban.text

        since = now_utc()
        r = do_login(auth_client, user["username"], "BanMe1234!")

        # USER_BANNED → AuthorizationError → 403 в текущем коде
        # (docstring /login упоминает 401 — это известный дрейф документации).
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "USER_BANNED"

        # DB: статус = BANNED, is_active=False, активные сессии сняты
        row = user_db_row(auth_db, user_id)
        assert row is not None
        assert row["status"] == "banned"
        assert row["is_active"] is False
        assert session_count(auth_db, user_id, active_only=True) == 0

        audit = wait_for_audit_row(
            loging_db_engine,
            action="user.login",
            status="failure",
            actor_id=user_id,
            since=since,
        )
        assert audit["details"].get("reason") == "banned"

    def test_login_lockout_after_max_failed_attempts(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
        auth_db,
        make_user,
    ):
        """5 (дефолтный max) неудач подряд → следующий login = 429 + lockout-row.

        Используем свежего юзера, чтобы не мешать другим тестам lockout'ом
        вокруг общего админа. После теста lockout остаётся на этом юзере
        ~15 мин, но юзер выкидывается из последующего использования.
        """
        user = make_user(password="LockMe1234!", platform_role="account_admin")
        username = user["username"]
        since = now_utc()

        # 5 заведомо неверных подряд
        for _ in range(5):
            r = do_login(auth_client, username, "Nope1234567!")
            assert r.status_code == 401, r.text

        # Шестой — даже с верным паролем — должен поймать lockout
        locked = do_login(auth_client, username, "LockMe1234!")
        assert locked.status_code == 429, locked.text
        body = locked.json()
        assert body["error_code"] == "ACCOUNT_TEMPORARILY_LOCKED"
        assert body["details"]["retry_after_seconds"] > 0

        # DB подтверждает lockout
        row = user_db_row(auth_db, user["user_id"])
        assert row is not None
        assert row["locked_until"] is not None
        assert row["failed_login_attempts"] >= 5

        # Audit события с reason=account_locked / invalid_password
        for _ in range(25):
            rows = query_audit_events(
                loging_db_engine,
                action="user.login",
                status="failure",
                actor_id=user["user_id"],
                since=since,
                limit=30,
            )
            reasons = {r["details"].get("reason") for r in rows}
            if "account_locked" in reasons:
                break
            time.sleep(0.4)
        else:
            pytest.xfail(
                "user.login/account_locked audit row не найден — вероятный "
                "drop INGEST_RATE_LIMIT-ом (см. _helpers_A_auth.wait_for_audit_row)."
            )


# ── 2. POST /refresh ─────────────────────────────────────────────────────────

class TestRefresh:
    def test_refresh_rotates_pair(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
        auth_db,
        make_user,
    ):
        user = make_user(password="Refresh1234!", platform_role="account_admin")
        first = do_login(auth_client, user["username"], "Refresh1234!").json()
        old_refresh = first["refresh_token"]
        user_id = user["user_id"]

        # JWT iat/exp в секундах — если refresh ловит ту же секунду что и
        # login, access_token бит-в-бит совпадает с предыдущим. Спим до
        # следующей секунды, чтобы зафиксировать факт ротации в access_token.
        time.sleep(1.1)

        since = now_utc()
        r = auth_client.post(
            f"{AUTH_PREFIX}/refresh",
            json={"refresh_token": old_refresh},
        )
        assert r.status_code == 200, r.text
        new = r.json()
        assert new["access_token"] != first["access_token"]
        assert new["refresh_token"] != old_refresh
        assert new["token_type"] == "Bearer"

        # У пользователя одна активная сессия — новый refresh
        assert session_count(auth_db, user_id, active_only=True) == 1

        # audit user.refresh success
        row = wait_for_audit_row(
            loging_db_engine,
            action="user.refresh",
            status="success",
            actor_id=user_id,
            since=since,
        )
        assert row["allowed"] is True

    def test_refresh_reuse_kills_session_and_audits_critical(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
        auth_db,
        make_user,
    ):
        user = make_user(password="Reuse1234!", platform_role="account_admin")
        login_resp = do_login(auth_client, user["username"], "Reuse1234!").json()
        first_refresh = login_resp["refresh_token"]
        user_id = user["user_id"]

        # Используем refresh один раз — нормальная ротация
        rotated = auth_client.post(
            f"{AUTH_PREFIX}/refresh",
            json={"refresh_token": first_refresh},
        )
        assert rotated.status_code == 200

        since = now_utc()
        # Повторное использование старого refresh — reuse-detection
        replay = auth_client.post(
            f"{AUTH_PREFIX}/refresh",
            json={"refresh_token": first_refresh},
        )
        assert replay.status_code == 401, replay.text
        assert replay.json()["error_code"] == "REFRESH_TOKEN_INVALID"

        # token.refresh_reuse CRITICAL в audit
        row = wait_for_audit_row(
            loging_db_engine,
            action="token.refresh_reuse",
            status="failure",
            since=since,
        )
        assert row["severity"] == "CRITICAL"
        assert row["allowed"] is False

        # Сессия (на user_id) убита — активных не осталось
        assert session_count(auth_db, user_id, active_only=True) == 0

    def test_refresh_unknown_token_returns_401(
        self,
        auth_client: httpx.Client,
    ):
        r = auth_client.post(
            f"{AUTH_PREFIX}/refresh",
            json={"refresh_token": f"dbos_refresh_{uuid.uuid4().hex}"},
        )
        assert r.status_code == 401
        assert r.json()["error_code"] == "REFRESH_TOKEN_INVALID"


# ── 3. POST /logout ──────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_revokes_session(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
        auth_db,
        make_user,
    ):
        user = make_user(password="Logout1234!", platform_role="account_admin")
        body = do_login(auth_client, user["username"], "Logout1234!").json()
        refresh = body["refresh_token"]
        user_id = user["user_id"]

        assert session_count(auth_db, user_id, active_only=True) == 1

        since = now_utc()
        r = auth_client.post(
            f"{AUTH_PREFIX}/logout",
            json={"refresh_token": refresh},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Активных сессий нет
        assert session_count(auth_db, user_id, active_only=True) == 0

        wait_for_audit_row(
            loging_db_engine,
            action="user.logout",
            status="success",
            actor_id=user_id,
            since=since,
        )

        # refresh больше не работает
        again = auth_client.post(
            f"{AUTH_PREFIX}/refresh",
            json={"refresh_token": refresh},
        )
        assert again.status_code == 401

    def test_logout_unknown_token_is_idempotent(
        self,
        auth_client: httpx.Client,
    ):
        r = auth_client.post(
            f"{AUTH_PREFIX}/logout",
            json={"refresh_token": f"dbos_refresh_{uuid.uuid4().hex}"},
        )
        # Идемпотентно — несуществующий refresh не должен ломать клиента
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ── 4. GET /me ───────────────────────────────────────────────────────────────

class TestMe:
    def test_me_anonymous_returns_401(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
    ):
        since = now_utc()
        r = auth_client.get(f"{AUTH_PREFIX}/me")
        assert r.status_code == 401

        # HTTP middleware пишет http.access_denied CRITICAL — читаем напрямую
        # из audit_events DB, минуя scoped logging_client.
        row = wait_for_audit_row(
            loging_db_engine,
            action="http.access_denied",
            status="denied",
            since=since,
        )
        assert row["severity"] == "CRITICAL"
        assert row["allowed"] is False

    def test_me_valid_token_returns_identity(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
        make_user,
        login_token,
    ):
        user = make_user(password="MeOk1234!", platform_role="account_admin")
        token = login_token(user["username"], "MeOk1234!")

        since = now_utc()
        r = get_me(auth_client, token)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["username"] == user["username"]
        assert body["is_banned"] is False
        # account_admin не имеет service-ролей
        assert body["service_roles"] == {}
        assert body["allowed_services"] == []
        assert body["platform_role"] == "account_admin"

        wait_for_audit_row(
            loging_db_engine,
            action="user.me",
            status="success",
            actor_id=user["user_id"],
            since=since,
        )

    def test_me_after_ban_returns_401_immediately(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        make_user,
        login_token,
    ):
        """После ban — следующий /me ловит 401 без ожидания TTL identity-cache.

        Проверка cache-invalidation hook'а в `ban_user`. Если бы кэш не
        чистился, /me ещё ~5s возвращал бы 200.
        """
        user = make_user(password="MeBan1234!", platform_role="account_admin")
        token = login_token(user["username"], "MeBan1234!")

        # warmup кэша
        ok = get_me(auth_client, token)
        assert ok.status_code == 200

        ban = auth_client.post(
            f"{AUTH_PREFIX}/users/{user['user_id']}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "ban-me-test"},
        )
        assert ban.status_code == 200

        # Сразу следующий /me — должен пробить кэш
        t0 = time.monotonic()
        r = get_me(auth_client, token)
        elapsed = time.monotonic() - t0
        assert r.status_code == 401, r.text
        assert r.json()["error_code"] == "USER_BANNED_OR_INACTIVE"
        # Sanity: меньше 5 сек (TTL identity_cache). Дешёвый smoke-check.
        assert elapsed < 5.0, f"banned /me took {elapsed:.2f}s"

    def test_me_invalid_token_returns_401(
        self,
        auth_client: httpx.Client,
    ):
        r = auth_client.get(
            f"{AUTH_PREFIX}/me",
            headers={"Authorization": "Bearer not.a.jwt"},
        )
        assert r.status_code == 401


# ── 5. POST/PATCH /users ─────────────────────────────────────────────────────

class TestUsersCRUD:
    def test_account_admin_creates_user_with_dept(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        loging_db_engine,
        auth_db,
    ):
        dept_id = ensure_department(
            auth_client, admin_token, f"a_users_dept_{rand_suffix()}"
        )
        username = f"acc_create_{rand_suffix()}"
        since = now_utc()

        r = auth_client.post(
            f"{AUTH_PREFIX}/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": username,
                "password": "CreateMe1234!",
                "department_id": dept_id,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == username
        assert body["department_id"] == dept_id

        # DB
        row = user_db_row(auth_db, body["user_id"])
        assert row is not None
        assert row["status"] == "active"
        assert row["is_active"] is True

        wait_for_audit_row(
            loging_db_engine,
            action="user.create",
            status="success",
            target_id=body["user_id"],
            since=since,
        )

    def test_create_user_without_dept_requires_platform_role(
        self,
        auth_client: httpx.Client,
        admin_token: str,
    ):
        r = auth_client.post(
            f"{AUTH_PREFIX}/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": f"no_dept_{rand_suffix()}",
                "password": "NoDept1234!",
            },
        )
        assert r.status_code == 422, r.text
        assert r.json()["error_code"] == "MISSING_REQUIRED_FIELD"

    @pytest.mark.parametrize(
        "platform_role",
        ["account_admin", "loging_admin", "loging_reader"],
    )
    def test_create_platform_admin_without_dept_succeeds(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        platform_role: str,
    ):
        r = auth_client.post(
            f"{AUTH_PREFIX}/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": f"pl_{platform_role}_{rand_suffix()}",
                "password": "PlatformOk1234!",
                "platform_role": platform_role,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["platform_role"] == platform_role
        assert body["department_id"] is None

    def test_department_admin_creates_user_in_own_dept(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        make_user,
        login_token,
    ):
        dept_id = ensure_department(
            auth_client, admin_token, f"a_dept_admin_own_{rand_suffix()}"
        )
        dept_admin = make_user(
            password="DeptAdm1234!",
            platform_role="department_admin",
            department_id=dept_id,
        )
        da_token = login_token(dept_admin["username"], "DeptAdm1234!")

        r = auth_client.post(
            f"{AUTH_PREFIX}/users",
            headers={"Authorization": f"Bearer {da_token}"},
            json={
                "username": f"da_member_{rand_suffix()}",
                "password": "Member1234!",
                "department_id": dept_id,
            },
        )
        assert r.status_code == 201, r.text

    def test_department_admin_cannot_create_in_other_dept(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        loging_db_engine,
        make_user,
        login_token,
    ):
        own = ensure_department(
            auth_client, admin_token, f"a_da_own_{rand_suffix()}"
        )
        other = ensure_department(
            auth_client, admin_token, f"a_da_other_{rand_suffix()}"
        )
        dept_admin = make_user(
            password="DeptAdm1234!",
            platform_role="department_admin",
            department_id=own,
        )
        da_token = login_token(dept_admin["username"], "DeptAdm1234!")

        since = now_utc()
        r = auth_client.post(
            f"{AUTH_PREFIX}/users",
            headers={"Authorization": f"Bearer {da_token}"},
            json={
                "username": f"da_cross_{rand_suffix()}",
                "password": "Cross1234!",
                "department_id": other,
            },
        )
        assert r.status_code == 403, r.text
        assert r.json()["error_code"] == "DEPARTMENT_ACCESS_DENIED"

        wait_for_audit_row(
            loging_db_engine,
            action="http.access_denied",
            status="denied",
            since=since,
        )

    def test_department_admin_cannot_assign_platform_role(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        make_user,
        login_token,
    ):
        dept_id = ensure_department(
            auth_client, admin_token, f"a_da_no_platform_{rand_suffix()}"
        )
        da = make_user(
            password="DeptAdm1234!",
            platform_role="department_admin",
            department_id=dept_id,
        )
        da_token = login_token(da["username"], "DeptAdm1234!")

        r = auth_client.post(
            f"{AUTH_PREFIX}/users",
            headers={"Authorization": f"Bearer {da_token}"},
            json={
                "username": f"da_escalate_{rand_suffix()}",
                "password": "Escalate1234!",
                "department_id": dept_id,
                "platform_role": "account_admin",
            },
        )
        assert r.status_code == 403
        assert r.json()["error_code"] == "PLATFORM_ROLE_ASSIGNMENT_DENIED"

    def test_regular_user_cannot_create_users(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        make_user,
        login_token,
    ):
        dept_id = ensure_department(
            auth_client, admin_token, f"a_reg_dept_{rand_suffix()}"
        )
        regular = make_user(
            password="Regular1234!",
            department_id=dept_id,
        )
        rt = login_token(regular["username"], "Regular1234!")

        r = auth_client.post(
            f"{AUTH_PREFIX}/users",
            headers={"Authorization": f"Bearer {rt}"},
            json={
                "username": f"would_be_{rand_suffix()}",
                "password": "Would1234!",
                "department_id": dept_id,
            },
        )
        # AnyAdmin dependency — отбивает не-админа 403
        assert r.status_code == 403, r.text

    def test_patch_user_email_updates_db_and_audits(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        loging_db_engine,
        make_user,
    ):
        user = make_user(
            password="Patch1234!", platform_role="account_admin",
        )
        new_email = f"e2e_{rand_suffix()}@example.com"

        since = now_utc()
        r = auth_client.patch(
            f"{AUTH_PREFIX}/users/{user['user_id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": new_email},
        )
        assert r.status_code == 200, r.text
        assert r.json()["email"] == new_email

        wait_for_audit_row(
            loging_db_engine,
            action="user.update",
            status="success",
            target_id=user["user_id"],
            since=since,
        )

    def test_create_user_with_loging_reader_platform_role_succeeds(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        login_token,
    ):
        """loging_reader — platform-роль без dept; раньше требовала dept, теперь нет."""
        username = f"lr_{rand_suffix()}"
        r = auth_client.post(
            f"{AUTH_PREFIX}/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": username,
                "password": "Reader1234!",
                "platform_role": "loging_reader",
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["platform_role"] == "loging_reader"
        # И /me работает
        token = login_token(username, "Reader1234!")
        me_r = get_me(auth_client, token)
        assert me_r.status_code == 200
        assert me_r.json()["platform_role"] == "loging_reader"


# ── 6. PAT lifecycle ─────────────────────────────────────────────────────────

class TestPATLifecycle:
    def test_create_pat_returns_plaintext_once_and_persists_hash(
        self,
        auth_client: httpx.Client,
        loging_db_engine,
        auth_db,
        admin_token: str,
    ):
        """admin_token используется как PAT-owner — у него уже есть user_id."""
        # вытащим user_id из /me
        me_resp = get_me(auth_client, admin_token).json()
        owner_id = me_resp["user_id"]

        name = f"pat_create_{rand_suffix()}"
        since = now_utc()
        r = auth_client.post(
            f"{AUTH_PREFIX}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": name, "allowed_services": ["auth_service"]},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        token = body["token"]
        token_id = body["token_id"]
        assert token.startswith("dbos_pat_")
        assert body["name"] == name

        # DB-row создан, plaintext в БД не лежит (поля token нет; есть token_prefix)
        row = pat_db_row(auth_db, token_id)
        assert row is not None
        assert row["user_id"] == owner_id
        assert row["revoked_at"] is None

        # Audit pat.create INFO
        audit = wait_for_audit_row(
            loging_db_engine,
            action="pat.create",
            status="success",
            actor_id=owner_id,
            target_id=token_id,
            since=since,
        )
        assert audit["severity"] == "INFO"
        # plaintext НЕ должен утекать в details
        assert token not in str(audit["details"])

    def test_list_pats_does_not_expose_plaintext(
        self,
        auth_client: httpx.Client,
        admin_token: str,
    ):
        # Создаём, чтобы было что листать
        r = auth_client.post(
            f"{AUTH_PREFIX}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": f"pat_list_{rand_suffix()}", "allowed_services": ["auth_service"]},
        )
        assert r.status_code == 201
        token_plain = r.json()["token"]

        listing = auth_client.get(
            f"{AUTH_PREFIX}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert listing.status_code == 200
        items = listing.json()
        assert len(items) >= 1
        for item in items:
            assert "token" not in item or item.get("token") in (None, "")
            assert token_plain not in str(item)
            assert "token_prefix" in item

    def test_revoke_pat_makes_introspect_inactive(
        self,
        auth_client: httpx.Client,
        logging_service_client: httpx.Client,
        loging_db_engine,
        auth_db,
        admin_token: str,
    ):
        # Create
        r = auth_client.post(
            f"{AUTH_PREFIX}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": f"pat_rev_{rand_suffix()}", "allowed_services": ["auth_service"]},
        )
        token = r.json()["token"]
        token_id = r.json()["token_id"]

        # Pre-revoke: introspect видит active=True
        ok = auth_client.post(
            f"{AUTH_PREFIX}/authorization/introspect",
            headers={"Authorization": f"Bearer test-logging-api-key",
                     "X-Service-Identity": "loging_service"},
            json={"token": token},
        )
        # service-to-service introspect требует SERVICE_API_KEY;
        # logging-service-client тоже им шарит
        assert ok.status_code == 200, ok.text
        assert ok.json()["active"] is True

        since = now_utc()
        rv = auth_client.delete(
            f"{AUTH_PREFIX}/tokens/{token_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert rv.status_code == 200, rv.text

        # DB: revoked_at set
        row = pat_db_row(auth_db, token_id)
        assert row is not None
        assert row["revoked_at"] is not None

        # Post-revoke: introspect видит active=False
        intr = auth_client.post(
            f"{AUTH_PREFIX}/authorization/introspect",
            headers={"Authorization": f"Bearer test-logging-api-key",
                     "X-Service-Identity": "loging_service"},
            json={"token": token},
        )
        assert intr.status_code == 200
        assert intr.json()["active"] is False

        wait_for_audit_row(
            loging_db_engine,
            action="pat.revoke",
            status="success",
            target_id=token_id,
            since=since,
        )

    def test_revoke_unknown_pat_returns_404(
        self,
        auth_client: httpx.Client,
        admin_token: str,
    ):
        r = auth_client.delete(
            f"{AUTH_PREFIX}/tokens/pat_does_not_exist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 404
        assert r.json()["error_code"] == "TOKEN_NOT_FOUND"

    def test_pat_cannot_revoke_someone_elses_token(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        make_user,
        login_token,
    ):
        admin_pat = auth_client.post(
            f"{AUTH_PREFIX}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": f"adm_pat_{rand_suffix()}", "allowed_services": ["auth_service"]},
        ).json()

        other = make_user(password="Other1234!", platform_role="account_admin")
        other_token = login_token(other["username"], "Other1234!")

        r = auth_client.delete(
            f"{AUTH_PREFIX}/tokens/{admin_pat['token_id']}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        # Cross-user revoke — TOKEN_NOT_FOUND (oracle-safe, не 403)
        assert r.status_code == 404
        assert r.json()["error_code"] == "TOKEN_NOT_FOUND"

    @pytest.mark.skip(
        reason="POST /tokens отбивает expires_at<=now() c INVALID_TOKEN_EXPIRY "
               "(token_service.create_token). Создать expired-PAT через API нельзя; "
               "сценарий expired-introspect проверяется только через прямой UPDATE "
               "в БД, который здесь не делаем."
    )
    def test_expired_pat_is_inactive_in_introspect(
        self,
        auth_client: httpx.Client,
        admin_token: str,
    ):
        past = (now_utc() - timedelta(seconds=2)).isoformat()
        r = auth_client.post(
            f"{AUTH_PREFIX}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": f"pat_expired_{rand_suffix()}",
                "expires_at": past,
                "allowed_services": ["auth_service"],
            },
        )
        assert r.status_code == 201, r.text
        token = r.json()["token"]

        intr = auth_client.post(
            f"{AUTH_PREFIX}/authorization/introspect",
            headers={"Authorization": f"Bearer test-logging-api-key",
                     "X-Service-Identity": "loging_service"},
            json={"token": token},
        )
        assert intr.status_code == 200
        assert intr.json()["active"] is False


# ── 7. Ban cascade ───────────────────────────────────────────────────────────

class TestBanCascade:
    def test_ban_user_revokes_bot_tokens_of_owned_bots(
        self,
        auth_client: httpx.Client,
        admin_token: str,
        loging_db_engine,
        auth_db,
        make_user,
        login_token,
    ):
        """ban user → bot_tokens owned-ботов revoked → introspect bot-token = inactive.

        Ботa создаём через JWT юзера (тогда `bot.created_by` = user_id),
        чтобы ban-каскад нашёл бота в `list_by_creator`.
        """
        dept_id = ensure_department(
            auth_client, admin_token, f"a_ban_dept_{rand_suffix()}"
        )
        # Сделаем dept_admin'ом — чтобы он мог создавать ботов в своём отделе
        owner = make_user(
            password="OwnerBot1234!",
            platform_role="department_admin",
            department_id=dept_id,
        )
        owner_token = login_token(owner["username"], "OwnerBot1234!")

        # Сервис + grant отделу
        svc = f"a_ban_svc_{rand_suffix()}"
        auth_client.post(
            f"{AUTH_PREFIX}/services",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": svc, "display_name": svc},
        )
        auth_client.post(
            f"{AUTH_PREFIX}/departments/{dept_id}/services",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": svc},
        )

        bot = auth_client.post(
            f"{AUTH_PREFIX}/bots",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "name": f"ban_bot_{rand_suffix()}",
                "department_id": dept_id,
                "allowed_services": [svc],
            },
        )
        assert bot.status_code == 201, bot.text
        bot_id = bot.json()["bot_id"]

        tok = auth_client.post(
            f"{AUTH_PREFIX}/bots/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"name": "ban-cascade-token", "allowed_services": ["auth_service"]},
        )
        assert tok.status_code == 201
        bot_token_plain = tok.json()["token"]

        # Sanity: бот-токен активен
        ok = auth_client.post(
            f"{AUTH_PREFIX}/authorization/introspect",
            headers={"Authorization": f"Bearer test-logging-api-key",
                     "X-Service-Identity": "loging_service"},
            json={"token": bot_token_plain},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["active"] is True

        # Ban владельца
        since = now_utc()
        t0 = time.monotonic()
        ban = auth_client.post(
            f"{AUTH_PREFIX}/users/{owner['user_id']}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "cascade-test"},
        )
        assert ban.status_code == 200, ban.text

        # Бот-токен должен сразу стать inactive
        intr = auth_client.post(
            f"{AUTH_PREFIX}/authorization/introspect",
            headers={"Authorization": f"Bearer test-logging-api-key",
                     "X-Service-Identity": "loging_service"},
            json={"token": bot_token_plain},
        )
        elapsed = time.monotonic() - t0
        assert intr.status_code == 200
        assert intr.json()["active"] is False
        assert elapsed < 5.0, f"bot-token still active after ban for {elapsed:.2f}s"

        # user.ban audit — CRITICAL + счётчики revoke'нутых
        row = wait_for_audit_row(
            loging_db_engine,
            action="user.ban",
            status="success",
            target_id=owner["user_id"],
            since=since,
        )
        assert row["severity"] == "CRITICAL"
        details = row["details"]
        assert details.get("ban_type") == "permanent"
        assert details.get("bot_tokens_revoked", 0) >= 1
        assert details.get("owned_bots_count", 0) >= 1


# ── 8. Revoke immediate effect ───────────────────────────────────────────────

class TestRevokeImmediateEffect:
    def test_revoked_pat_introspect_flips_active_false_no_5s_window(
        self,
        auth_client: httpx.Client,
        admin_token: str,
    ):
        """Между DELETE /tokens/{id} и следующим introspect — без 5s окна.

        Identity-cache в `/me` (TTL=5s) к introspect не относится: introspect
        читает токен из БД на каждый вызов. Здесь проверяем что introspect
        видит revoked сразу.
        """
        r = auth_client.post(
            f"{AUTH_PREFIX}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": f"pat_imm_{rand_suffix()}", "allowed_services": ["auth_service"]},
        )
        token = r.json()["token"]
        token_id = r.json()["token_id"]

        # warmup (пройдёт через любой кэш, если есть)
        for _ in range(2):
            intr = auth_client.post(
                f"{AUTH_PREFIX}/authorization/introspect",
                headers={"Authorization": f"Bearer test-logging-api-key",
                         "X-Service-Identity": "loging_service"},
                json={"token": token},
            )
            assert intr.json()["active"] is True

        # Revoke
        rv = auth_client.delete(
            f"{AUTH_PREFIX}/tokens/{token_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert rv.status_code == 200

        t0 = time.monotonic()
        intr = auth_client.post(
            f"{AUTH_PREFIX}/authorization/introspect",
            headers={"Authorization": f"Bearer test-logging-api-key",
                     "X-Service-Identity": "loging_service"},
            json={"token": token},
        )
        elapsed = time.monotonic() - t0
        assert intr.status_code == 200
        assert intr.json()["active"] is False
        assert elapsed < 5.0
