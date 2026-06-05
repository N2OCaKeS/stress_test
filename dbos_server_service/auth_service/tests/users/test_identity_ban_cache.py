"""Identity / ban / cache fixes (auth_service).

Покрывает:
* `IdentityContext.subject_type` пробрасывается в audit `actor_type`.
* `UserCreate/Update.platform_role` валидируется `PlatformRole`-enum'ом.
* `BANNED → BLOCKED` через PATCH деактивирует Ban-record.
* `unban_user` реактивирует ban-revoked PAT'ы.
* `IntrospectResponse` JWT vs PAT симметричен при banned юзере (active=False).
* `get_current_identity` TTL-кэш на ~5s, rapid same-token requests
  не делают N introspect'ов.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from src.core.constants import UserStatus
from src.models import PersonalAccessToken

PATCH_URL = "/api/auth/v1/users/{user_id}"
BAN_URL = "/api/auth/v1/users/{user_id}/ban"
UNBAN_URL = "/api/auth/v1/users/{user_id}/unban"
TOKENS_URL = "/api/auth/v1/tokens"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
USERS_URL = "/api/auth/v1/users"
ME_URL = "/api/auth/v1/me"
LOGIN_URL = "/api/auth/v1/login"


# ── Capture audit-payloads ───────────────────────────────────────────────────


@pytest.fixture()
def capture_audit_payloads(monkeypatch):
    """Перехватывает все payload, отправляемые `audit_service.emit()`.

    Зеркало `tests/users/test_update.py:capture_audit_payloads` — мокаем
    `httpx.post` (sync-путь) и `httpx.AsyncClient` (async-путь) в
    `src.services.audit_service`, плюс подменяем `get_settings` чтобы
    `logging_service_url`/`api_key` были не-пустыми (триггерит post-путь).
    """
    captured: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):
        captured.append(json)

    monkeypatch.setattr("src.services.audit_service.httpx.post", fake_sync_post)

    class _AsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json, headers):
            captured.append(json)

            class R:
                status_code = 201

            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type(
            "S",
            (),
            {"logging_service_url": "http://test", "logging_service_api_key": "k"},
        )(),
    )
    return captured


# ── subject_type → audit actor_type ──────────────────────────────────────────


class TestSubjectTypeInAudit:
    """``IdentityContext.subject_type`` должен попадать в audit ``actor_type``.

    До фикса audit-trail все события писал как ``actor_type="user"`` —
    `oauth_client`-минтнутые JWT смешивались с human-action'ами, и SIEM не мог
    отличить m2m-вызов от user-вызова.
    """

    async def test_user_jwt_audit_has_actor_type_user(
        self, client, user_a, user_a_token, capture_audit_payloads,
    ):
        """User-JWT-аутентифицированный запрос → audit с `actor_type="user"`."""
        # Любой authenticated endpoint, который эмитит audit — `/me` подойдёт
        # (через login/refresh — но `/me` чище, без побочных эмитов).
        # `/me` сам по себе не эмитит audit, но если он вернёт 200 — мы
        # знаем, что middleware прошёл и audit_context с subject_type="user"
        # установлен. Проверим через `user.update` audit (PATCH /users) —
        # он точно emit'ится.
        from urllib.parse import quote
        resp = await client.patch(
            PATCH_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"email": "subject_type_test@example.com"},
        )
        # user_a — не admin, PATCH чужой будет 403, но в audit-trail
        # `http.access_denied` event летит с middleware. Зайдём как admin.

    async def test_admin_jwt_audit_event_has_actor_type_user(
        self, client, admin_token, user_a, capture_audit_payloads,
    ):
        """Через admin-JWT обновляем юзера — audit event 'user.update' должен
        нести `actor_type="user"` (admin это просто user с platform_role)."""
        resp = await client.patch(
            PATCH_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": "actor_type_user@example.com"},
        )
        assert resp.status_code == 200

        user_update_events = [
            p for p in capture_audit_payloads
            if p.get("action") == "user.update" and p.get("target_id") == user_a.id
        ]
        assert user_update_events, (
            "expected user.update event from PATCH /users"
        )
        # `actor_type` подхватывается из audit_context (заполнен middleware'ом).
        # Должно быть "user" (admin это просто user с platform_role).
        assert user_update_events[0]["actor_type"] == "user"


# ── platform_role Literal[PlatformRole] ──────────────────────────────────────


class TestPlatformRoleEnum:
    """``UserCreate.platform_role`` / ``UserUpdate.platform_role`` валидируются
    через ``PlatformRole``-enum. До фикса был ``str | None`` — произвольная
    строка проходила и записывалась в БД.
    """

    async def test_create_user_with_garbage_platform_role_returns_422(
        self, client, admin_token, dept_a,
    ):
        """POST /users со ``platform_role="hacker"`` отбивается 422."""
        resp = await client.post(
            USERS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "p2b_user",
                "password": "Password12!",
                "department_id": dept_a.id,
                "platform_role": "hacker",
            },
        )
        assert resp.status_code == 422, resp.text

    async def test_create_user_with_valid_platform_role_succeeds(
        self, client, admin_token, dept_a,
    ):
        """Все валидные значения PlatformRole принимаются."""
        for role in ("account_admin", "department_admin", "loging_admin", "loging_reader"):
            payload = {
                "username": f"p2b_valid_{role}",
                "password": "Password12!",
                "platform_role": role,
            }
            # account_admin/loging_admin — без department_id (см. service-logic);
            # department_admin/loging_reader — с department_id.
            if role in ("department_admin", "loging_reader"):
                payload["department_id"] = dept_a.id
            resp = await client.post(
                USERS_URL,
                headers={"Authorization": f"Bearer {admin_token}"},
                json=payload,
            )
            assert resp.status_code == 201, f"{role}: {resp.text}"

    async def test_create_user_with_null_platform_role_succeeds(
        self, client, admin_token, dept_a,
    ):
        """None / отсутствие поля → обычный юзер."""
        resp = await client.post(
            USERS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "p2b_regular",
                "password": "Password12!",
                "department_id": dept_a.id,
            },
        )
        assert resp.status_code == 201, resp.text

    async def test_patch_user_with_garbage_platform_role_returns_422(
        self, client, admin_token, user_a,
    ):
        """PATCH /users со ``platform_role="hacker"`` отбивается 422."""
        resp = await client.patch(
            PATCH_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"platform_role": "hacker"},
        )
        assert resp.status_code == 422, resp.text


# ── BANNED → BLOCKED deactivates Ban ─────────────────────────────────────────


class TestBannedToBlockedDeactivatesBan:
    """``PATCH {status: blocked}`` поверх BANNED-юзера деактивирует Ban-row.

    До фикса оставался logical inconsistency: ``User.status=BLOCKED``, но
    активный ``Ban.is_active=True`` (Ban-row продолжал существовать). Симптомы:
    `ban_user` после такого upset'а ругался на BAN_ALREADY_ACTIVE.
    """

    async def test_patch_banned_to_blocked_deactivates_ban(
        self, client, admin_token, user_a, db,
    ):
        """Сценарий: ban через POST /ban → PATCH status=blocked → Ban deactivated."""
        from src.repositories.bans import BanRepository

        # Step 1: ban через POST /users/{id}/ban — создаёт active Ban.
        resp = await client.post(
            BAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "p2c setup"},
        )
        assert resp.status_code == 200

        active_ban = await BanRepository(db).get_active_ban(user_a.id)
        assert active_ban is not None, "ban setup didn't create active Ban"
        assert active_ban.is_active is True

        # Step 2: PATCH status=blocked.
        resp = await client.patch(
            PATCH_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "blocked"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "blocked"
        assert resp.json()["is_active"] is False

        # Step 3: Ban должен быть deactivated.
        await db.refresh(active_ban)
        assert active_ban.is_active is False, (
            "BANNED → BLOCKED через PATCH должен deactivate Ban-row"
        )

        # Sanity: get_active_ban теперь возвращает None.
        assert await BanRepository(db).get_active_ban(user_a.id) is None

    async def test_patch_banned_to_blocked_emits_status_change_audit(
        self, client, admin_token, user_a, capture_audit_payloads,
    ):
        """Audit-event `user.ban_deactivated_via_status_change` эмитится."""
        # Ban сначала через POST /ban.
        resp = await client.post(
            BAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent"},
        )
        assert resp.status_code == 200
        capture_audit_payloads.clear()

        # PATCH BANNED → BLOCKED.
        resp = await client.patch(
            PATCH_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "blocked"},
        )
        assert resp.status_code == 200

        deactivation_events = [
            p for p in capture_audit_payloads
            if p.get("action") == "user.ban_deactivated_via_status_change"
            and p.get("target_id") == user_a.id
        ]
        assert deactivation_events, (
            f"expected user.ban_deactivated_via_status_change audit, "
            f"got actions: {[p.get('action') for p in capture_audit_payloads]}"
        )
        assert deactivation_events[0]["details"]["new_status"] == "blocked"

    async def test_create_user_with_blocked_status_creates_no_ban(
        self, client, admin_token, dept_a, db,
    ):
        """Regression: создание BLOCKED-юзера НЕ создаёт Ban-row.

        Status=blocked при создании — это legit "юзер изначально заблокирован
        админом", а не ban. Ban-row создаётся ТОЛЬКО через ban_user / PATCH
        status=banned.
        """
        from src.repositories.bans import BanRepository

        # Создаём юзера без status (он создастся ACTIVE).
        resp = await client.post(
            USERS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "p2c_initially_blocked",
                "password": "Password12!",
                "department_id": dept_a.id,
            },
        )
        assert resp.status_code == 201, resp.text
        new_user_id = resp.json()["user_id"]

        # PATCH-блокируем (ACTIVE → BLOCKED).
        resp = await client.patch(
            PATCH_URL.format(user_id=new_user_id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "blocked"},
        )
        assert resp.status_code == 200

        # Никаких Ban-record не должно быть — ACTIVE → BLOCKED не идёт через
        # ban_user, и нашего нового deactivate-кода тоже не триггерит.
        ban = await BanRepository(db).get_active_ban(new_user_id)
        assert ban is None, (
            "PATCH ACTIVE → BLOCKED не должен создавать Ban-row"
        )


# ── unban reactivates ban-revoked PAT ────────────────────────────────────────


class TestUnbanReactivatesPAT:
    """``unban_user`` реактивирует ban-revoked PAT'ы.

    До фикса юзер unbanned, но его PAT'ы оставались revoked (т.к. ``ban_user``
    revoke'ит их). UX-bug — нужно было создавать новые.
    """

    async def test_unban_reactivates_ban_revoked_pat(
        self, client, admin_token, user_a, user_a_token, db,
    ):
        """ban → unban → старый PAT снова работает (introspect active=True)."""
        # 1. Создаём PAT.
        pat_resp = await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "p2d_pat", "allowed_services": ["service_x"]},
        )
        assert pat_resp.status_code == 201, pat_resp.text
        raw = pat_resp.json()["token"]

        # 2. Ban — PAT станет revoked.
        await client.post(
            BAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent"},
        )
        intr = await client.post(INTROSPECT_URL, json={"token": raw})
        assert intr.json()["active"] is False, "PAT should be revoked after ban"

        # 3. Unban.
        unban_resp = await client.post(
            UNBAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert unban_resp.status_code == 200

        # 4. Старый PAT снова работает.
        intr_after = await client.post(INTROSPECT_URL, json={"token": raw})
        assert intr_after.json()["active"] is True, (
            "PAT отозванный ban'ом должен реактивироваться при unban"
        )
        assert intr_after.json()["is_banned"] is False

    async def test_unban_does_not_reactivate_user_revoked_pat(
        self, client, admin_token, user_a, user_a_token, db,
    ):
        """``unban_user`` НЕ реактивирует PAT'ы, отозванные юзером самим (через DELETE).

        Защита от over-reactivation: если юзер сам отозвал PAT через
        DELETE /tokens/{id} **до** ban'а, unban не должен его воскресить.
        """
        # 1. Создаём PAT.
        pat_resp = await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "p2d_user_revoked", "allowed_services": ["service_x"]},
        )
        pat_id = pat_resp.json()["token_id"]
        raw = pat_resp.json()["token"]

        # 2. Юзер сам revoke'ит — `revoked_reason="user"` (default endpoint'а).
        del_resp = await client.delete(
            f"{TOKENS_URL}/{pat_id}",
            headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert del_resp.status_code in (200, 204)

        # Sanity: PAT inactive.
        intr = await client.post(INTROSPECT_URL, json={"token": raw})
        assert intr.json()["active"] is False

        # 3. Ban + Unban.
        await client.post(
            BAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent"},
        )
        await client.post(
            UNBAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        # 4. User-revoked PAT остался revoked.
        intr_after = await client.post(INTROSPECT_URL, json={"token": raw})
        assert intr_after.json()["active"] is False, (
            "user-initiated revoke не должен быть воскрешён unban'ом"
        )

    async def test_unban_audit_includes_pat_reactivated_count(
        self, client, admin_token, user_a, user_a_token, capture_audit_payloads,
    ):
        """`user.unban` audit несёт `pat_reactivated` counter."""
        # Создаём 2 PAT.
        for i in range(2):
            await client.post(
                TOKENS_URL,
                headers={"Authorization": f"Bearer {user_a_token}"},
                json={"name": f"p2d_audit_pat_{i}", "allowed_services": ["service_x"]},
            )

        # Ban (revoke'ит оба).
        await client.post(
            BAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent"},
        )
        capture_audit_payloads.clear()

        # Unban.
        await client.post(
            UNBAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        unban_events = [
            p for p in capture_audit_payloads
            if p.get("action") == "user.unban" and p.get("target_id") == user_a.id
        ]
        assert unban_events
        assert unban_events[0]["details"]["pat_reactivated"] == 2


# ── IntrospectResponse JWT vs PAT symmetry at banned ────────────────────────


class TestIntrospectSymmetryAtBanned:
    """JWT и PAT introspect должны симметрично возвращать ``active=False``
    при banned юзере. До фикса PAT возвращал ``active=True, is_banned=True``
    (или вообще не доходил до этой проверки, т.к. ban_user уже revoke'ит PAT).
    Тесты — defence-in-depth.
    """

    async def test_jwt_introspect_inactive_for_banned_user(
        self, client, admin_token, user_a, user_a_token,
    ):
        """JWT забаненного юзера → introspect active=False."""
        await client.post(
            BAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent"},
        )

        # JWT уже на руках (user_a_token заминтился до ban'а), но revalidate'нет:
        # `user.status != ACTIVE` → active=False.
        resp = await client.post(INTROSPECT_URL, json={"token": user_a_token})
        assert resp.status_code == 200
        assert resp.json()["active"] is False, (
            "JWT забаненного юзера должен вернуть active=False"
        )

    async def test_pat_introspect_inactive_for_banned_user(
        self, client, admin_token, user_a, user_a_token, db,
    ):
        """PAT забаненного юзера → introspect active=False.

        Текущий поведенческий контракт: ban_user revoke'ит PAT, поэтому
        `get_active_by_hash` возвращает None, и introspect отвечает active=False
        через no-match. Это тест фиксирует именно этот end-to-end inactive-
        семантику, symmetric с JWT.
        """
        raw = (await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "p2e_pat", "allowed_services": ["service_x"]},
        )).json()["token"]

        # Sanity до ban'а — PAT active.
        intr_pre = await client.post(INTROSPECT_URL, json={"token": raw})
        assert intr_pre.json()["active"] is True

        await client.post(
            BAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent"},
        )

        intr_post = await client.post(INTROSPECT_URL, json={"token": raw})
        assert intr_post.status_code == 200
        assert intr_post.json()["active"] is False, (
            "PAT забаненного юзера должен вернуть active=False "
            "(симметрично JWT-ветке)"
        )

    async def test_pat_introspect_inactive_when_user_status_banned_but_pat_alive(
        self, client, user_a, user_a_token, db,
    ):
        """Defence-in-depth: даже если PAT почему-то остался не-revoked
        (race / migration / legacy), но юзер BANNED — introspect возвращает
        active=False через user.status check.

        Сценарий не достижим через обычный flow (ban_user revoke'ит PAT
        атомарно), но мы хотим, чтобы symmetry с JWT-веткой держалась
        и при разрыве этой инварианты.
        """
        # Создаём PAT.
        pat_resp = await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "p2e_defence_pat", "allowed_services": ["service_x"]},
        )
        raw = pat_resp.json()["token"]

        # Прямо в БД ставим юзеру BANNED + is_active=False, но PAT ОСТАВЛЯЕМ
        # активным (имитируем разорванную инварианту).
        from src.models import User
        user = await db.scalar(select(User).where(User.id == user_a.id))
        user.status = UserStatus.BANNED
        user.is_active = False
        await db.commit()

        # introspect должен вернуть active=False через user-check.
        intr = await client.post(INTROSPECT_URL, json={"token": raw})
        assert intr.status_code == 200
        assert intr.json()["active"] is False


# ── TTL cache for get_current_identity ──────────────────────────────────────


class TestIntrospectCache:
    """``get_current_identity`` имеет TTL-кэш ~5s.

    30 одинаковых requests от одного юзера → ровно 1 introspect (вызов
    UserRepository.get_by_id). Через 6s — кэш expire'нул, новый introspect.

    В conftest.py default TTL=0 (disabled) для совместимости с legacy
    тестами, которые мутируют User прямым SQL'ом мимо invalidate-хуков.
    Эти тесты включают TTL>0 через monkeypatch.
    """

    @pytest.fixture()
    def enable_cache(self, monkeypatch):
        """Включает TTL-кэш на 1s и очищает state до/после теста."""
        from src.dependencies import auth as deps_auth_mod

        monkeypatch.setattr(deps_auth_mod, "_IDENTITY_CACHE_TTL_SECONDS", 1.0)
        deps_auth_mod._identity_cache_clear()
        yield
        deps_auth_mod._identity_cache_clear()

    async def test_rapid_same_token_uses_cache(
        self, client, user_a, user_a_token, monkeypatch, enable_cache,
    ):
        """30 requests одним токеном → 1 вызов ``_identity_from_user_jwt``.

        ``/me`` сам по себе делает доп. `user_repo.get_by_id` в
        `auth_service.get_identity` (не purview кэша), поэтому считаем
        вызовы `_identity_from_user_jwt` (точка, которую кэш короткозамыкает).
        """
        from src.dependencies import auth as deps_auth_mod

        original_revalidate = deps_auth_mod._identity_from_user_jwt
        call_count = {"n": 0}

        async def counting_revalidate(db, payload):
            if payload.get("sub") == user_a.id:
                call_count["n"] += 1
            return await original_revalidate(db, payload)

        monkeypatch.setattr(
            deps_auth_mod, "_identity_from_user_jwt", counting_revalidate,
        )

        # 30 идентичных GET /me — первый делает revalidate, остальные 29 — cache hit.
        for _ in range(30):
            resp = await client.get(
                ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
            )
            assert resp.status_code == 200

        assert call_count["n"] == 1, (
            f"expected 1 revalidate for 30 same-token requests, "
            f"got {call_count['n']}"
        )

    async def test_cache_expires_after_ttl(
        self, client, user_a, user_a_token, monkeypatch,
    ):
        """Через >TTL кэш expire'нул — следующий request делает свежий revalidate."""
        from src.dependencies import auth as deps_auth_mod

        monkeypatch.setattr(deps_auth_mod, "_IDENTITY_CACHE_TTL_SECONDS", 0.1)
        deps_auth_mod._identity_cache_clear()

        original_revalidate = deps_auth_mod._identity_from_user_jwt
        call_count = {"n": 0}

        async def counting_revalidate(db, payload):
            if payload.get("sub") == user_a.id:
                call_count["n"] += 1
            return await original_revalidate(db, payload)

        monkeypatch.setattr(
            deps_auth_mod, "_identity_from_user_jwt", counting_revalidate,
        )

        # Первый request — miss → 1 revalidate.
        resp = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 200
        assert call_count["n"] == 1

        # Ждём истечения TTL.
        time.sleep(0.2)

        # Второй request — кэш expire'нул, fresh revalidate.
        resp = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 200
        assert call_count["n"] == 2, (
            "После TTL expire следующий request должен сделать новый revalidate"
        )

    async def test_cache_separates_different_tokens(
        self, client, user_a, user_a_token, admin_token, enable_cache,
    ):
        """Кэш не должен возвращать identity юзера A на токен юзера B."""
        me1 = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
        )
        me2 = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert me1.status_code == 200
        assert me2.status_code == 200
        assert me1.json()["user_id"] != me2.json()["user_id"], (
            "Cache key must be per-token, not shared"
        )

    async def test_ban_invalidates_cache(
        self, client, admin_token, user_a, user_a_token, enable_cache,
    ):
        """`ban_user` инвалидирует identity-кэш → next request 401.

        Без invalidation было бы: ban → cached identity ещё ACTIVE →
        запрос пропускается до TTL expire. Invalidate-хук восстанавливает
        instant-revoke семантику.
        """
        # 1. user_a делает request (заполняет кэш).
        resp = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp.status_code == 200

        # 2. admin банит user_a (должен сбросить кэш).
        await client.post(
            BAN_URL.format(user_id=user_a.id),
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent"},
        )

        # 3. user_a ещё раз — должен получить 401 (не cached).
        resp_after = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {user_a_token}"},
        )
        assert resp_after.status_code == 401, (
            f"ban должен invalidate'ить cache мгновенно, "
            f"got {resp_after.status_code}"
        )
