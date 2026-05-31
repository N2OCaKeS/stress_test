"""Тесты: PATCH /api/auth/v1/users/{user_id} — обновление пользователя."""

import pytest

from src.core.constants import UserStatus

URL = "/api/auth/v1/users/{user_id}"
TOKENS_URL = "/api/auth/v1/tokens"
INTROSPECT_URL = "/api/auth/v1/authorization/introspect"
LOGIN_URL = "/api/auth/v1/login"


@pytest.fixture()
def capture_audit_payloads(monkeypatch):
    """Перехватывает все payload, отправляемые `audit_service.emit()`.

    Использует тот же паттерн, что и `tests/core/test_audit_integration.py` —
    monkeypatch на `httpx.post` (sync-путь) и `httpx.AsyncClient` (async-путь)
    внутри `src.services.audit_service`, плюс подмена `get_settings`, чтобы
    `logging_service_url`/`api_key` всегда были не-пустыми.
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


async def _patch(client, token, user_id, body):
    return await client.patch(URL.format(user_id=user_id),
                              headers={"Authorization": f"Bearer {token}"}, json=body)


# ── account_admin ─────────────────────────────────────────────────────────────


async def test_admin_updates_user_email(client, admin_token, user_a):
    resp = await _patch(client, admin_token, user_a.id, {"email": "new@example.com"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "new@example.com"


async def test_admin_updates_user_status(client, admin_token, user_a):
    resp = await _patch(client, admin_token, user_a.id, {"status": "blocked"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"


async def test_admin_moves_user_to_another_department(client, admin_token, user_a, dept_b):
    resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
    assert resp.status_code == 200
    assert resp.json()["department_id"] == dept_b.id


async def test_update_nonexistent_user_returns_404(client, admin_token):
    resp = await _patch(client, admin_token, "usr_does_not_exist", {"email": "x@x.com"})
    assert resp.status_code == 404


# ── department_admin / regular ────────────────────────────────────────────────


async def test_dept_admin_a_cannot_update_user_in_dept_b(client, dept_admin_a_token, user_b):
    resp = await _patch(client, dept_admin_a_token, user_b.id, {"email": "x@x.com"})
    assert resp.status_code == 403


async def test_regular_user_cannot_update_user(client, user_a_token, user_b):
    resp = await _patch(client, user_a_token, user_b.id, {"email": "x@x.com"})
    assert resp.status_code == 403


async def test_invalid_email_returns_422(client, admin_token, user_a):
    resp = await _patch(client, admin_token, user_a.id, {"email": "not-an-email"})
    assert resp.status_code == 422


# ── status <-> is_active sync через PATCH /users ─────────────────────────────
#
# ban_user/unban_user закрывают is_active sync только в этих двух методах.
# Альтернативный путь PATCH /users/{id} {"status": "banned"} оставлял
# is_active=True, из-за чего authorization_service.introspect для PAT возвращал
# is_banned=False — обход ban-defence.
# Дополнительно: schemas/users.py:UserUpdate.status был str (не Literal/Enum),
# что позволяло admin прописать произвольную строку.


async def test_patch_status_banned_syncs_is_active_to_false(client, admin_token, user_a, db):
    """PATCH /users/{id} со status="banned" должен синхронизировать is_active=False."""
    resp = await _patch(client, admin_token, user_a.id, {"status": "banned"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "banned"
    assert resp.json()["is_active"] is False

    await db.refresh(user_a)
    assert user_a.status == UserStatus.BANNED
    assert user_a.is_active is False


async def test_patch_status_blocked_syncs_is_active_to_false(client, admin_token, user_a, db):
    """PATCH /users/{id} со status="blocked" также сбрасывает is_active (только ACTIVE → True)."""
    resp = await _patch(client, admin_token, user_a.id, {"status": "blocked"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"
    assert resp.json()["is_active"] is False

    await db.refresh(user_a)
    assert user_a.status == UserStatus.BLOCKED
    assert user_a.is_active is False


async def test_patch_status_active_syncs_is_active_to_true(client, admin_token, user_a, db):
    """Возврат в active через PATCH восстанавливает is_active=True."""
    # Сначала забаним через тот же путь.
    await _patch(client, admin_token, user_a.id, {"status": "banned"})
    await db.refresh(user_a)
    assert user_a.is_active is False

    # Теперь вернём активность.
    resp = await _patch(client, admin_token, user_a.id, {"status": "active"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    assert resp.json()["is_active"] is True

    await db.refresh(user_a)
    assert user_a.status == UserStatus.ACTIVE
    assert user_a.is_active is True


async def test_patch_status_garbage_returns_422(client, admin_token, user_a):
    """Произвольная строка должна отлетать на Pydantic-валидации (Enum)."""
    resp = await _patch(client, admin_token, user_a.id, {"status": "garbage"})
    assert resp.status_code == 422


async def test_patch_status_banned_revokes_pat(
    client, admin_token, user_a, user_a_token,
):
    """PATCH со status="banned" делегирует в `ban_user`, который revoke-ит PAT.

    Раньше PATCH-путь мог оставить PAT активным («тихий бан»). После фикса
    PATCH-ACTIVE→BANNED проходит через `ban_user` → `token_repo.revoke_all_for_user`,
    и introspect возвращает `active=False`.
    """
    raw = (await client.post(
        TOKENS_URL,
        headers={"Authorization": f"Bearer {user_a_token}"},
        json={"name": "patch_ban_intr_pat", "allowed_services": ["service_x"]},
    )).json()["token"]

    # Sanity: PAT валиден до бана.
    pre = await client.post(INTROSPECT_URL, json={"token": raw})
    assert pre.status_code == 200
    assert pre.json()["active"] is True

    resp = await _patch(client, admin_token, user_a.id, {"status": "banned"})
    assert resp.status_code == 200

    post = await client.post(INTROSPECT_URL, json={"token": raw})
    assert post.status_code == 200
    assert post.json()["active"] is False  # но юзер забанен


# ── PATCH /users {status: banned/active} должен эмитить user.ban / user.unban ──
#
# До фикса PATCH-путь делал «тихий бан»: status в БД менялся, is_active
# синхронизировался, НО:
#   1. летел audit `user.update` вместо `user.ban` — мониторинг по `user.ban`
#      терял этих юзеров;
#   2. `Ban`-record не создавался — `BanRepository.get_active_ban` возвращал None,
#      `ban_user` мог быть вызван повторно поверх «тихо забаненного» юзера;
#   3. сессии не отзывались — refresh_token продолжал работать после PATCH.
# Фикс: при переходе ACTIVE → BANNED делегируем в `ban_user`, ACTIVE ← BANNED —
# в `unban_user`. BLOCKED — обычный update.


class TestPatchStatusEmitsBanAudit:
    async def test_patch_status_banned_emits_user_ban_not_user_update(
        self, client, admin_token, user_a, capture_audit_payloads,
    ):
        """PATCH со status="banned" должен эмитить `user.ban`, а не `user.update`."""
        resp = await _patch(client, admin_token, user_a.id, {"status": "banned"})
        assert resp.status_code == 200

        # Audit-trail должен содержать ровно `user.ban` для целевого user_a,
        # без сопровождающего `user.update` (раньше эмитился — теперь нет).
        ban_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.ban" and p.get("target_id") == user_a.id
        ]
        update_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.update" and p.get("target_id") == user_a.id
        ]
        assert len(ban_events) == 1, f"expected 1 user.ban, got {len(ban_events)}"
        assert update_events == [], (
            f"expected no user.update for PATCH-ban, got {update_events}"
        )

    async def test_patch_status_banned_revokes_sessions(
        self, client, admin_token, user_a,
    ):
        """PATCH со status="banned" должен отзывать refresh-сессии (как `ban_user`)."""
        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        raw_refresh = login.json()["refresh_token"]

        resp = await _patch(client, admin_token, user_a.id, {"status": "banned"})
        assert resp.status_code == 200

        refresh = await client.post(
            "/api/auth/v1/refresh", json={"refresh_token": raw_refresh},
        )
        # `ban_user.revoke_all_for_user` отозвал сессию — refresh обязан упасть.
        assert refresh.status_code in (401, 403)

    async def test_patch_status_active_from_banned_emits_user_unban(
        self, client, admin_token, user_a, db, capture_audit_payloads,
    ):
        """PATCH со status="active" из BANNED должен эмитить `user.unban`."""
        # Сначала PATCH-баним (тоже через delegation — Ban-record создан).
        await _patch(client, admin_token, user_a.id, {"status": "banned"})
        await db.refresh(user_a)
        assert user_a.status == UserStatus.BANNED

        capture_audit_payloads.clear()

        resp = await _patch(client, admin_token, user_a.id, {"status": "active"})
        assert resp.status_code == 200

        unban_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.unban" and p.get("target_id") == user_a.id
        ]
        update_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.update" and p.get("target_id") == user_a.id
        ]
        assert len(unban_events) == 1, f"expected 1 user.unban, got {len(unban_events)}"
        assert update_events == [], (
            f"expected no user.update for PATCH-unban, got {update_events}"
        )

    async def test_patch_status_blocked_still_emits_user_update(
        self, client, admin_token, user_a, capture_audit_payloads,
    ):
        """BLOCKED — это не ban; audit-trail остаётся `user.update`."""
        resp = await _patch(client, admin_token, user_a.id, {"status": "blocked"})
        assert resp.status_code == 200

        update_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.update" and p.get("target_id") == user_a.id
        ]
        ban_events = [
            p for p in capture_audit_payloads
            if p["action"] in ("user.ban", "user.unban") and p.get("target_id") == user_a.id
        ]
        assert len(update_events) == 1, f"expected 1 user.update, got {len(update_events)}"
        assert ban_events == [], (
            f"BLOCKED не должен эмитить user.ban/user.unban, got {ban_events}"
        )
        # Проверяем что details.changes несёт новый статус.
        assert update_events[0]["details"]["changes"]["status"] == "blocked"

    async def test_patch_status_banned_creates_ban_record(
        self, client, admin_token, user_a, db,
    ):
        """После делегации PATCH→ban_user в БД должен существовать активный `Ban`-record."""
        resp = await _patch(client, admin_token, user_a.id, {"status": "banned"})
        assert resp.status_code == 200

        from src.repositories.bans import BanRepository
        ban = await BanRepository(db).get_active_ban(user_a.id)
        assert ban is not None, "PATCH-ban должен создавать Ban-record (через ban_user)"
        assert ban.ban_type == "permanent"

    async def test_patch_email_and_status_banned_emits_only_user_ban(
        self, client, admin_token, user_a, capture_audit_payloads,
    ):
        """Совмещённый PATCH (email + status="banned") — `user.ban` + один `user.update`
        для email-поля. Status уже обработан делегацией, в `user.update` его быть не
        должно (audit-trail не дублирует ban-семантику)."""
        resp = await _patch(
            client, admin_token, user_a.id,
            {"email": "banned_email@example.com", "status": "banned"},
        )
        assert resp.status_code == 200

        ban_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.ban" and p.get("target_id") == user_a.id
        ]
        update_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.update" and p.get("target_id") == user_a.id
        ]
        assert len(ban_events) == 1
        # email-изменение остаётся как `user.update` — но без status в changes.
        assert len(update_events) == 1
        changes = update_events[0]["details"]["changes"]
        assert changes.get("email") == "banned_email@example.com"
        assert "status" not in changes, (
            "status уже обработан через user.ban, дублировать в user.update нельзя"
        )


# ── PATCH /users atomicity: один commit на запрос ────────────────────────────
#
# Раньше `update_user` делал ДВА `db.commit()` при PATCH с
# status=banned/active + другими полями:
#   1. внутри `ban_user`/`unban_user` — фиксирует Ban + status + revoke sessions;
#   2. трейлинговый — фиксирует email/department_id/platform_role.
# Crash между ними оставлял user в partial state (забанен, email не обновлён,
# `user.update`-audit утерян).
# Фикс: `ban_user`/`unban_user(commit=False)` возвращают pending-audit,
# `update_user` делает ОДИН финальный commit, потом эмитит обе audit'ы.


class TestPatchAtomicity:
    async def test_combined_ban_and_email_uses_single_commit(
        self, client, admin_token, user_a, monkeypatch,
    ):
        """PATCH {email, status=banned} → ровно один `db.commit()`-call.

        Counter-инжектим в `AsyncSession.commit` — патчим bound-method на
        конкретной сессии, которая прокидывается в `client` через
        `dependency_overrides[get_db]`. `db`-фикстура — наш SAVEPOINT-обёрнутый
        AsyncSession, monkeypatch перехватывает оригинальный `commit` и
        проксирует на него.
        """
        from sqlalchemy.ext.asyncio import AsyncSession

        commit_calls: list[int] = []
        original_commit = AsyncSession.commit

        async def counting_commit(self):
            commit_calls.append(1)
            return await original_commit(self)

        monkeypatch.setattr(AsyncSession, "commit", counting_commit)

        resp = await _patch(
            client, admin_token, user_a.id,
            {"email": "atomic@example.com", "status": "banned"},
        )
        assert resp.status_code == 200, resp.text

        # До фикса было ≥2 (один в `ban_user`, один трейлинговый).
        # После фикса — ровно один, покрывающий и Ban-side, и email-update.
        # NB: проверяем «один commit на сам PATCH». В test-harness'е
        # SAVEPOINT-обёртка может коммитить вокруг (savepoint release ≠ outer
        # commit), но `update_user` в itself должен вызвать commit() **ровно один
        # раз**. Если каркас добавит свои, отметка «1» останется для
        # `update_user`-инициированного commit'а; в проде это и есть наш единый
        # transactional boundary.
        assert len(commit_calls) == 1, (
            f"expected exactly 1 db.commit() for PATCH(ban+email), got {len(commit_calls)}"
        )

    async def test_ban_only_patch_uses_single_commit(
        self, client, admin_token, user_a, monkeypatch,
    ):
        """PATCH {status=banned} (без других полей) тоже использует один
        commit — раньше commit был внутри `ban_user`, теперь — единственный
        финальный в `update_user`."""
        from sqlalchemy.ext.asyncio import AsyncSession

        commit_calls: list[int] = []
        original_commit = AsyncSession.commit

        async def counting_commit(self):
            commit_calls.append(1)
            return await original_commit(self)

        monkeypatch.setattr(AsyncSession, "commit", counting_commit)

        resp = await _patch(client, admin_token, user_a.id, {"status": "banned"})
        assert resp.status_code == 200

        assert len(commit_calls) == 1, (
            f"PATCH status=banned должен делать ровно 1 commit, got {len(commit_calls)}"
        )

    async def test_unban_via_patch_uses_single_commit(
        self, client, admin_token, user_a, db, monkeypatch,
    ):
        """PATCH {status=active} из BANNED → unban_user(commit=False) + один
        финальный commit. До фикса было 2 (внутренний в unban_user + внешний
        для оставшихся полей, даже если их не было)."""
        # Сначала забаним стандартным путём (без счётчика).
        await _patch(client, admin_token, user_a.id, {"status": "banned"})
        await db.refresh(user_a)

        from sqlalchemy.ext.asyncio import AsyncSession

        commit_calls: list[int] = []
        original_commit = AsyncSession.commit

        async def counting_commit(self):
            commit_calls.append(1)
            return await original_commit(self)

        monkeypatch.setattr(AsyncSession, "commit", counting_commit)

        resp = await _patch(client, admin_token, user_a.id, {"status": "active"})
        assert resp.status_code == 200

        assert len(commit_calls) == 1, (
            f"PATCH status=active (unban) должен делать ровно 1 commit, "
            f"got {len(commit_calls)}"
        )


# ── PATCH department_id → purge UserServiceRole ──────────────────────────────
#
# При смене `department_id` старые `UserServiceRole`-строки указывают на сервисы
# прежнего отдела. `_merge_permissions` INTERSECT отфильтровывает их из
# effective view (runtime-correctness OK), но физически `is_active=True`-строки
# остаются в БД — инвариант «роль ⊆ сервисы отдела» нарушен, admin-UI и
# audit-reports путаются.
# Фикс: при `department_id` change → `role_repo.deactivate_all_for_user`
# ПЕРЕД сохранением нового dept + audit `user.roles_purged_on_transfer`
# (severity=WARNING) с count'ом затронутых строк.


class TestPatchDepartmentTransferPurgesRoles:
    async def test_dept_change_deactivates_all_user_service_roles(
        self, client, admin_token, user_a, dept_b, db,
    ):
        """PATCH {department_id=other} → все active `UserServiceRole` юзера → is_active=False."""
        from src.repositories.roles import RoleRepository

        role_repo = RoleRepository(db)
        # Sanity: user_a-фикстура выдала ему `reader` на service_x.
        before = await role_repo.list_active_assignments(user_a.id)
        assert len(before) >= 1, "user_a должен начинать с активными ролями"

        resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
        assert resp.status_code == 200, resp.text
        assert resp.json()["department_id"] == dept_b.id

        after = await role_repo.list_active_assignments(user_a.id)
        assert after == [], (
            f"после transfer все service-роли должны быть деактивированы, "
            f"осталось: {[(r.service_name, r.role) for r in after]}"
        )

    async def test_no_dept_change_keeps_roles_intact(
        self, client, admin_token, user_a, db,
    ):
        """PATCH без `department_id` (только email) роли не трогает."""
        from src.repositories.roles import RoleRepository

        role_repo = RoleRepository(db)
        before = await role_repo.list_active_assignments(user_a.id)
        before_keys = sorted((r.service_name, r.role) for r in before)
        assert before_keys, "sanity: user_a начинает с активными ролями"

        resp = await _patch(
            client, admin_token, user_a.id, {"email": "still_in_dept@example.com"},
        )
        assert resp.status_code == 200

        after = await role_repo.list_active_assignments(user_a.id)
        after_keys = sorted((r.service_name, r.role) for r in after)
        assert after_keys == before_keys, (
            f"роли без смены department_id трогать нельзя: "
            f"было {before_keys}, стало {after_keys}"
        )

    async def test_same_department_id_keeps_roles_intact(
        self, client, admin_token, user_a, db,
    ):
        """PATCH {department_id=current} (no-op) — не должен purge'ить роли."""
        from src.repositories.roles import RoleRepository

        role_repo = RoleRepository(db)
        before_keys = sorted(
            (r.service_name, r.role)
            for r in await role_repo.list_active_assignments(user_a.id)
        )
        assert before_keys, "sanity"

        resp = await _patch(
            client, admin_token, user_a.id,
            {"department_id": user_a.department_id},
        )
        assert resp.status_code == 200

        after_keys = sorted(
            (r.service_name, r.role)
            for r in await role_repo.list_active_assignments(user_a.id)
        )
        assert after_keys == before_keys, (
            "идемпотентный PATCH (тот же department_id) не должен purge'ить роли"
        )

    async def test_dept_change_emits_roles_purged_audit(
        self, client, admin_token, user_a, dept_b, capture_audit_payloads,
    ):
        """`user.roles_purged_on_transfer` audit-event эмитится ровно один раз
        с count затронутых строк, from/to department_id и target_username."""
        # Запоминаем оригинальный dept_id до PATCH'а — после успешного transfer
        # SQLAlchemy identity-map обновит атрибут на ORM-объекте, и сравнить
        # его уже не с чем.
        original_dept_id = user_a.department_id
        original_username = user_a.username

        resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
        assert resp.status_code == 200

        purged_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.roles_purged_on_transfer"
            and p.get("target_id") == user_a.id
        ]
        assert len(purged_events) == 1, (
            f"expected 1 user.roles_purged_on_transfer, got {len(purged_events)}"
        )
        details = purged_events[0]["details"]
        assert details["from_department_id"] == original_dept_id
        assert details["to_department_id"] == dept_b.id
        assert details["roles_purged_count"] >= 1
        assert details["target_username"] == original_username

    async def test_no_dept_change_does_not_emit_roles_purged_audit(
        self, client, admin_token, user_a, capture_audit_payloads,
    ):
        """PATCH без смены department_id не эмитит `user.roles_purged_on_transfer`."""
        resp = await _patch(client, admin_token, user_a.id, {"email": "x2@example.com"})
        assert resp.status_code == 200

        purged_events = [
            p for p in capture_audit_payloads
            if p["action"] == "user.roles_purged_on_transfer"
        ]
        assert purged_events == [], (
            f"purge-audit не должен эмититься без смены dept, got {purged_events}"
        )


# ── PATCH status ACTIVE↔BANNED требует account_admin ─────────────────────────
#
# PATCH /users/{id} ходит под `AnyAdmin` (account_admin или department_admin),
# но POST /users/{id}/ban и /unban — только под `AccountAdmin`. Без явной
# проверки на смену status="banned"/"active" department_admin мог бы
# банить/анбанить юзеров своего отдела через PATCH в обход guard'а.


async def test_dept_admin_cannot_ban_via_patch_status(client, dept_admin_a_token, user_a):
    """department_admin не может выставить status=banned через PATCH /users/{id}."""
    resp = await _patch(client, dept_admin_a_token, user_a.id, {"status": "banned"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["error_code"] == "STATUS_CHANGE_REQUIRES_ACCOUNT_ADMIN"


async def test_dept_admin_cannot_unban_via_patch_status(
    client, admin_token, dept_admin_a_token, user_a,
):
    """department_admin не может выставить status=active при текущем BANNED через PATCH."""
    pre = await _patch(client, admin_token, user_a.id, {"status": "banned"})
    assert pre.status_code == 200

    resp = await _patch(client, dept_admin_a_token, user_a.id, {"status": "active"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["error_code"] == "STATUS_CHANGE_REQUIRES_ACCOUNT_ADMIN"


async def test_dept_admin_can_set_blocked_via_patch_status(
    client, dept_admin_a_token, user_a, db,
):
    """BLOCKED не lifecycle-критичный (не ban) — department_admin может."""
    resp = await _patch(client, dept_admin_a_token, user_a.id, {"status": "blocked"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"
    await db.refresh(user_a)
    assert user_a.status == UserStatus.BLOCKED


async def test_account_admin_can_ban_via_patch_status(client, admin_token, user_a, db):
    """account_admin путь — должен остаться разрешённым (регрессия)."""
    resp = await _patch(client, admin_token, user_a.id, {"status": "banned"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "banned"
    await db.refresh(user_a)
    assert user_a.status == UserStatus.BANNED


# ── cross-department transfer guard ───────────────────────────────────────────


async def test_dept_admin_cannot_transfer_user_to_other_dept(
    client, dept_admin_a_token, user_a, dept_b,
):
    """DA dept_a → PATCH user_a (своего отдела) {"department_id": dept_b}.

    Guard verifies actor.dept == user.dept, не new dept — без явной проверки
    DA мог бы перевыкинуть юзера из своего отдела в чужой без согласия
    target-dept'а. Cross-dept transfer должен быть привилегией только
    account_admin'а.
    """
    resp = await _patch(
        client, dept_admin_a_token, user_a.id, {"department_id": dept_b.id},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "USER_UPDATE_FORBIDDEN"


async def test_dept_admin_same_dept_patch_without_dept_change_still_works(
    client, dept_admin_a_token, user_a, dept_a,
):
    """DA своего отдела PATCH'ит user_a с тем же department_id — должно работать
    (idempotent no-op). Гард не должен ловить self-transfer'ы."""
    resp = await _patch(
        client, dept_admin_a_token, user_a.id, {"department_id": dept_a.id},
    )
    assert resp.status_code == 200, resp.text


async def test_account_admin_can_still_transfer_user_cross_dept(
    client, admin_token, user_a, dept_b,
):
    """account_admin не должен попадать под cross-dept guard — регрессия,
    зеркало test_admin_moves_user_to_another_department выше."""
    resp = await _patch(client, admin_token, user_a.id, {"department_id": dept_b.id})
    assert resp.status_code == 200
    assert resp.json()["department_id"] == dept_b.id
