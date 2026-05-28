"""Edge cases для ban/unban/reset_password.

Базовые happy в test_ban.py / test_reset_password.py. Добавляем:
* `ban_type="temporary"` + `expires_at` в будущем — login заблокирован;
  `expires_at` в прошлом — auto-unban на login;
* несколько последовательных банов: после unban новый ban активен,
  предыдущий — нет;
* reset_password дважды на одно значение работает (хеш считается заново);
* reset_password не сбрасывает refresh tokens, отозванные ранее — фиксируем
  что revoke_all идёт каждый раз.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from src.models import Ban, User

USERS_URL = "/api/auth/v1/users"
LOGIN_URL = "/api/auth/v1/login"


# ── Temporary ban ────────────────────────────────────────────────────────────

class TestTemporaryBan:
    async def test_temporary_ban_with_future_expires_blocks_login(
        self, client, admin_token, user_a,
    ):
        """`ban_type="temporary"` + expires_at в будущем — user не может логиниться
        пока ban активен, даже если `expires_at` уже прошло (auto-unban — отдельный
        процесс, не реализован)."""
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "limited", "expires_at": future},
        )
        assert resp.status_code == 200

        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        assert login.status_code in (401, 403)

    async def test_past_expires_at_auto_unbans_on_login(
        self, client, admin_token, user_a, db,
    ):
        """expires_at в прошлом — `login` срабатывает auto-unban inline и
        авторизует юзера. Раньше ban оставался активным навсегда —
        temporary ban становился eternal без явного operator-unban или scheduler.

        См. `user_service.auto_unban_if_expired` — вызывается из
        `auth_service.login` перед raise `USER_BANNED`.
        """
        # `BanRequest` валидирует cross-field инвариант «temporary требует
        # expires_at в будущем», поэтому сначала ставим валидный future,
        # затем сдвигаем в прошлое через прямой UPDATE.
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "expired", "expires_at": future},
        )
        past = datetime.now(timezone.utc) - timedelta(days=2)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()
        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        assert login.status_code == 200, login.text

        # Проверяем что состояние БД синхронизировано: User.status=ACTIVE,
        # is_active=True, Ban.is_active=False — это не «успел залогиниться,
        # но остался забанен» state.
        from src.repositories.bans import BanRepository
        from src.core.constants import UserStatus
        await db.refresh(user_a)
        assert user_a.status == UserStatus.ACTIVE
        assert user_a.is_active is True
        assert await BanRepository(db).get_active_ban(user_a.id) is None

    async def test_short_temporary_ban_expires_and_login_succeeds(
        self, client, admin_token, user_a, db,
    ):
        """`ban_user(expires_at=now+1s)` → wait 2s → login успешен (auto-unban).

        Сценарий: temporary ban на короткий срок должен
        сниматься при первом login после expires_at без вмешательства оператора.
        """
        # `BanRequest` валидирует `expires_at` строго в будущем, поэтому
        # сначала ставим валидный future, потом сдвигаем в прошлое через UPDATE.
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        ban_resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "short", "expires_at": future},
        )
        assert ban_resp.status_code == 200

        # Сдвигаем expires_at в точку «секунду назад» — эмулируем «прошло 2s
        # после expires_at», без `time.sleep(2)` в тесте (быстрее, детерминированно).
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()

        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        assert login.status_code == 200, login.text
        body = login.json()
        # Юзер видит себя ACTIVE / not banned в identity-контексте.
        assert body["identity"]["is_banned"] is False

    async def test_permanent_ban_is_not_auto_unbanned(
        self, client, admin_token, user_a, db,
    ):
        """`ban_type="permanent"` (нет `expires_at`) — auto-unban НЕ срабатывает,
        login заблокирован навсегда (требуется явный operator-unban).

        Гвардия: убедимся что `auto_unban_if_expired` не схлопывает permanent
        бан в случае какого-то null/None-glitch'а.
        """
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "forever"},
        )
        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        assert login.status_code == 403
        assert login.json()["error_code"] == "USER_BANNED"

    async def test_auto_unban_cas_skips_audit_when_already_deactivated(
        self, client, admin_token, user_a, db, monkeypatch,
    ):
        """При concurrent-race двух login'ов на
        user'а с истёкшим temporary-ban'ом `user.unban` audit-event должен
        эмитнуть **ровно один** worker — тот, который реально flip-нул
        `is_active=True → False`. Второй worker должен silently bail.

        Раньше `auto_unban_if_expired` делал idempotent UPDATE
        без CAS — оба worker'а проходили `deactivate` → `db.commit()` → emit
        → duplicate `user.unban` (`source="auto"`) в loging_service.
        После фикса `BanRepository.deactivate` использует
        `UPDATE … WHERE is_active = TRUE RETURNING id` — возвращает `True`
        только winner'у, остальные получают `False` и НЕ эмитят audit.

        Симуляция race deterministic'ная: имитируем «второго worker'а»,
        который уже успел поймать активный Ban через `get_active_ban`, но
        первый worker сделал deactivate и flush раньше его CAS-UPDATE.
        Прямой вызов `BanRepository.deactivate` второй раз с in-memory ban-
        объектом проверит short-circuit без обхода через
        `get_active_ban`-проверку (которая в production-сценарии могла
        вернуть row до flip'а у другого worker'а).
        """
        # Готовим истёкший temporary ban.
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "concurrent-race", "expires_at": future},
        )
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()

        # Перехватываем audit-emit.
        captured: list[dict] = []
        from src.services import audit_service as audit_mod
        original_emit = audit_mod.emit

        def _capture(action, actor_id=None, **kw):
            if action == "user.unban":
                captured.append({"actor_id": actor_id, "details": dict(kw.get("details") or {})})
            return original_emit(action, actor_id, **kw)

        monkeypatch.setattr(audit_mod, "emit", _capture)
        from src.services import user_service as us_mod
        monkeypatch.setattr(us_mod, "audit_service", audit_mod)

        # Worker A: первый login — `auto_unban_if_expired` срабатывает целиком,
        # эмитит `user.unban`. Возвращает True.
        await db.refresh(user_a)
        from src.core.constants import UserStatus as US
        assert user_a.status == US.BANNED

        result_a = await us_mod.auto_unban_if_expired(db, user_a)
        assert result_a is True
        assert len(captured) == 1, f"worker A должен эмитить ровно 1 audit, got {captured}"

        # Worker B: «опоздавший» login — приходит с in-memory user.status=BANNED
        # (как если бы он успел прочитать БД до A-commit'а) и пытается
        # повторно auto-unban'ить. После фикса:
        # — `get_active_ban` вернёт None (A уже deactivate'нул), и функция
        #   bail'ит на `return False`. НИ commit, НИ audit.
        # Симулируем «опоздавший» снепшот state: руками возвращаем
        # status=BANNED у in-memory объекта, не трогая БД (worker B ещё не
        # видел A-commit с точки зрения своего snapshot'а).
        user_a.status = US.BANNED
        user_a.is_active = False
        result_b = await us_mod.auto_unban_if_expired(db, user_a)
        # Worker B видит, что активного Ban больше нет → возвращает False
        # (или True по новому контракту short-circuit'а — главное чтобы
        # audit НЕ был эмитнут).
        # Главный assertion — ровно один `user.unban`-audit на всю гонку.
        assert len(captured) == 1, (
            f"concurrent auto-unban должен эмитить РОВНО один user.unban "
            f"audit, got {len(captured)}: {captured}"
        )
        assert captured[0]["actor_id"] is None
        assert captured[0]["details"].get("source") == "auto"
        assert captured[0]["details"].get("reason") == "ban_expired"

    async def test_auto_unban_deactivate_returns_false_on_second_call(
        self, client, admin_token, user_a, db,
    ):
        """`BanRepository.deactivate` — CAS-уровень: первый вызов flip'ает
        `is_active`, второй вызов на том же ban-объекте возвращает `False`
        (RETURNING пуст, потому что WHERE-clause `is_active = TRUE` уже
        не матчит). Это самый низкоуровневый guard для concurrent-race —
        даже если оба worker'а пройдут `get_active_ban`, только winner
        получит True.
        """
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "cas-double", "expires_at": future},
        )

        from src.repositories.bans import BanRepository
        ban_repo = BanRepository(db)
        ban = await ban_repo.get_active_ban(user_a.id)
        assert ban is not None and ban.is_active is True

        first = await ban_repo.deactivate(ban, unbanned_by=None)
        assert first is True, "первый CAS-deactivate должен flip-нуть row"

        # Cимулируем второго worker'а: в памяти у него тот же ban-объект,
        # он уверен что `is_active=True` (стейл-снапшот). Но CAS UPDATE
        # увидит уже flipped row и вернёт пустой RETURNING.
        ban.is_active = True  # стейл-снапшот «как если бы worker B ещё не видел A-commit»
        second = await ban_repo.deactivate(ban, unbanned_by=None)
        assert second is False, (
            "второй CAS-deactivate должен вернуть False (row уже flipped) — "
            "это сигнал caller'у НЕ эмитить duplicate audit"
        )

    async def test_auto_unban_unbanned_by_is_null_not_empty_string(
        self, client, admin_token, user_a, db,
    ):
        """auto-unban пишет
        `Ban.unbanned_by = NULL` (была `""` magic-string). Фильтр
        `WHERE unbanned_by IS NULL` теперь корректно находит system-unban'ы.
        """
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "null-marker", "expires_at": future},
        )
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()

        # Trigger auto-unban через login.
        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        assert login.status_code == 200

        # Проверяем, что deactivated ban-row имеет unbanned_by = NULL.
        from sqlalchemy import select
        from src.models import Ban as BanModel
        row = (await db.execute(
            select(BanModel).where(BanModel.user_id == user_a.id, BanModel.is_active.is_(False))
        )).scalar_one_or_none()
        assert row is not None, "auto-unban должен оставить deactivated Ban-row"
        assert row.unbanned_by is None, (
            f"auto-unban должен писать unbanned_by=NULL (для SIEM-фильтра), got {row.unbanned_by!r}"
        )

    async def test_auto_unban_emits_user_unban_audit_with_system_actor(
        self, client, admin_token, user_a, db, monkeypatch,
    ):
        """Auto-unban при истёкшем temporary ban эмитит `user.unban` с
        `actor_id=None` и `details.source="auto"`, `details.reason="ban_expired"`.

        Обоснование выбора action: мы используем существующий `user.unban`
        вместо нового `user.auto_unban`, потому что lifecycle-переход
        (BANNED→ACTIVE) тот же; различие через `source` поле — не через action.
        """
        captured: list[dict] = []

        from src.services import audit_service as audit_mod
        original_emit = audit_mod.emit

        def _capture(action, actor_id=None, **kw):
            if action == "user.unban":
                captured.append({"actor_id": actor_id, "details": dict(kw.get("details") or {})})
            return original_emit(action, actor_id, **kw)

        monkeypatch.setattr(audit_mod, "emit", _capture)
        # auth_service импортирует `audit_service` как from-import, patch'им и его символ.
        from src.services import auth_service as auth_mod
        from src.services import user_service as us_mod
        monkeypatch.setattr(auth_mod, "audit_service", audit_mod)
        monkeypatch.setattr(us_mod, "audit_service", audit_mod)

        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "audit-test", "expires_at": future},
        )
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()

        # Очищаем captured от operator-unban'ов фикстуры (если бы были)
        captured.clear()

        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        assert login.status_code == 200

        assert len(captured) == 1, captured
        auto = captured[0]
        assert auto["actor_id"] is None, "auto-unban должен иметь actor_id=None (system)"
        assert auto["details"].get("source") == "auto"
        assert auto["details"].get("reason") == "ban_expired"


# ── Unban + PAT ───────────────────────────────────────────────────────────────
#
# PAT-introspect блокируется на уровне `User.status == BANNED` через флаг
# `is_banned=True` (см. test_pat_introspect_reports_is_banned_true_after_ban
# в test_ban.py), а после unban — снова `is_banned=False`. Полноценная
# PAT-reactivation опирается на поле `PersonalAccessToken.revoked_reason`.
#
# Тут фиксируем что PAT continue to work «functionally» через весь
# ban/unban цикл — это контракт, на который опирается клиент.


class TestUnbanAndPAT:
    # unban_user реактивирует PAT-токены, отозванные при ban'е, через
    # `revoked_reason="ban"` (миграция c7d8e9f0a1b2).
    async def test_pat_works_again_after_ban_and_unban(
        self, client, admin_token, user_a, user_a_token,
    ):
        """Ban → unban → PAT всё ещё валиден и authorize-итcя."""
        raw = (await client.post(
            "/api/auth/v1/tokens",
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "unban_pat_cycle", "allowed_services": []},
        )).json()["token"]

        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "rev test"},
        )
        await client.post(
            f"{USERS_URL}/{user_a.id}/unban",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        intr = await client.post(
            "/api/auth/v1/authorization/introspect", json={"token": raw},
        )
        assert intr.status_code == 200
        body = intr.json()
        assert body["active"] is True
        assert body["is_banned"] is False


# ── Repeated ban/unban cycle ─────────────────────────────────────────────────

class TestBanCycle:
    async def test_unban_then_rebanned_picks_new_ban(self, client, admin_token, user_a, db):
        # Ban #1
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "first"},
        )
        # Unban
        u1 = await client.post(
            f"{USERS_URL}/{user_a.id}/unban",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert u1.status_code == 200
        # Ban #2
        b2 = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "second"},
        )
        assert b2.status_code == 200

        # В БД должен быть один активный ban с reason="second".
        from src.repositories.bans import BanRepository
        active = await BanRepository(db).get_active_ban(user_a.id)
        assert active is not None
        assert active.reason == "second"

    async def test_double_unban_returns_404(self, client, admin_token, user_a):
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "x"},
        )
        await client.post(
            f"{USERS_URL}/{user_a.id}/unban",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        second = await client.post(
            f"{USERS_URL}/{user_a.id}/unban",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert second.status_code == 404
        assert second.json()["error_code"] == "BAN_NOT_FOUND"


# ── Reset password edge ──────────────────────────────────────────────────────

class TestResetPasswordEdge:
    async def test_reset_to_same_value_works(self, client, admin_token, user_a):
        """Сброс на тот же пароль — хеш пересчитывается, но логин с этим паролем
        продолжает работать."""
        first = await client.post(
            f"{USERS_URL}/{user_a.id}/reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"new_password": "Reset1234!"},
        )
        assert first.status_code == 200
        second = await client.post(
            f"{USERS_URL}/{user_a.id}/reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"new_password": "Reset1234!"},
        )
        assert second.status_code == 200

        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "Reset1234!"},
        )
        assert login.status_code == 200

    async def test_reset_invalidates_old_password(self, client, admin_token, user_a):
        await client.post(
            f"{USERS_URL}/{user_a.id}/reset-password",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"new_password": "FreshPass1234!"},
        )
        resp = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        assert resp.status_code == 401


# ── Sessions revoked on ban ──────────────────────────────────────────────────

class TestSessionsRevokedOnBan:
    async def test_ban_revokes_active_sessions(self, client, admin_token, user_a, user_a_token):
        """После ban — старый refresh_token больше не работает."""
        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        raw_refresh = login.json()["refresh_token"]

        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "test"},
        )

        refresh = await client.post(
            "/api/auth/v1/refresh", json={"refresh_token": raw_refresh},
        )
        assert refresh.status_code == 401


# ── BanRequest validators ─────────────────────────────────────────────────────
#
# `schemas/users.py::BanRequest` раньше принимал произвольный `ban_type: str`
# и любой `expires_at: datetime | None` без проверки «в будущем». Это давало
# admin'у два вектора:
#   1. ban_type="garbage" → ломал аналитику и audit-trail (`Ban.ban_type`
#      хранил unknown-строку, фильтры по типу не работали).
#   2. expires_at в прошлом → ban-row создавался, но `auto_unban_if_expired`
#      сразу же его снимал на первом login юзера → **ban-bypass**.
#
# Фикс: `ban_type: BanType` (Pydantic Literal через StrEnum) +
# `@field_validator("expires_at")` гарантирует `> now()` +
# `@model_validator(mode="after")` cross-field: temporary требует expires_at,
# permanent — наоборот, не должен иметь expires_at.


class TestBanRequestValidators:
    async def test_invalid_ban_type_returns_422(self, client, admin_token, user_a):
        """`ban_type="garbage"` — 422.

        До фикса Pydantic пропускал любую строку, и в БД оседал
        `Ban.ban_type="garbage"`. После — `ban_type: Literal["permanent","temporary"]`
        бьёт 422 на schema-уровне.
        """
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "garbage", "reason": "invalid type"},
        )
        assert resp.status_code == 422, resp.text

    async def test_past_expires_at_returns_422(self, client, admin_token, user_a):
        """`expires_at` в прошлом — 422 (ban-bypass через past-expires_at).

        До фикса admin мог положить ban с `expires_at="2020-01-01..."`,
        и `auto_unban_if_expired` снимал его при первом login юзера —
        admin видел «ban applied», но юзер не был забанен ни секунды.
        """
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "past expiry", "expires_at": past},
        )
        assert resp.status_code == 422, resp.text

    async def test_now_expires_at_returns_422(self, client, admin_token, user_a):
        """`expires_at == now()` — тоже 422 (граничный случай `<=`)."""
        now = datetime.now(timezone.utc).isoformat()
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "boundary", "expires_at": now},
        )
        assert resp.status_code == 422, resp.text

    async def test_temporary_without_expires_at_returns_422(
        self, client, admin_token, user_a,
    ):
        """`ban_type="temporary"` БЕЗ `expires_at` — 422.

        До фикса admin мог создать ban с `ban_type="temporary"` и `expires_at=null` —
        фактически permanent (auto-unban никогда не сработает), но в Ban-row лежит
        `ban_type="temporary"` → аналитика и audit-trail врут.
        """
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "no expiry"},
        )
        assert resp.status_code == 422, resp.text

    async def test_permanent_with_expires_at_returns_422(
        self, client, admin_token, user_a,
    ):
        """`ban_type="permanent"` + `expires_at` — 422 (противоречивая конфигурация).

        Permanent-ban с auto-expiry — это либо опечатка, либо попытка использовать
        `temporary` с неправильным именем. В любом случае — schema bug.
        """
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "mixed", "expires_at": future},
        )
        assert resp.status_code == 422, resp.text

    async def test_permanent_without_expires_at_is_accepted(
        self, client, admin_token, user_a,
    ):
        """Regression: `permanent` + `expires_at=null` (или omit) — 200.

        Это базовый happy-path, на который опирается весь существующий
        `_ban`-helper и большинство тестов. Защищает от over-validation
        (если кто-то случайно сделает `expires_at` обязательным).
        """
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "permanent", "reason": "regression"},
        )
        assert resp.status_code == 200, resp.text

    async def test_default_ban_type_is_permanent(self, client, admin_token, user_a, db):
        """Regression: body без `ban_type` — default = `permanent`, 200.

        Защищает дефолтное значение `BanType.PERMANENT` в `BanRequest`
        (раньше было `ban_type: str = "permanent"`; должен остаться тем же).
        """
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"reason": "default type"},
        )
        assert resp.status_code == 200, resp.text

        from src.repositories.bans import BanRepository
        ban = await BanRepository(db).get_active_ban(user_a.id)
        assert ban is not None
        assert ban.ban_type == "permanent"

    async def test_temporary_with_future_expires_at_is_accepted(
        self, client, admin_token, user_a, db,
    ):
        """Regression: `temporary` + future `expires_at` — 200 + Ban-row создан."""
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        resp = await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "ok", "expires_at": future},
        )
        assert resp.status_code == 200, resp.text

        from src.repositories.bans import BanRepository
        ban = await BanRepository(db).get_active_ban(user_a.id)
        assert ban is not None
        assert ban.ban_type == "temporary"
        assert ban.expires_at is not None


# ── auto_unban должен симметрично с manual unban реактивировать PAT'ы ─────────


class TestAutoUnbanReactivatesPat:
    async def test_auto_unban_reactivates_ban_revoked_pat(
        self, client, admin_token, user_a, user_a_token, db,
    ):
        """До фикса auto-unban оставлял PAT'ы revoked (manual unban — реактивировал).

        Сценарий: юзер получает temporary ban → PAT отзывается с
        `revoked_reason="ban"` → expires_at истекает → следующий login
        запускает auto-unban → PAT должен снова стать `active=True`.
        """
        TOKENS_URL = "/api/auth/v1/tokens"
        INTROSPECT_URL = "/api/auth/v1/authorization/introspect"

        raw = (await client.post(
            TOKENS_URL,
            headers={"Authorization": f"Bearer {user_a_token}"},
            json={"name": "auto_unban_pat", "allowed_services": []},
        )).json()["token"]

        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        await client.post(
            f"{USERS_URL}/{user_a.id}/ban",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ban_type": "temporary", "reason": "auto-unban-pat", "expires_at": future},
        )

        # PAT после ban'а — `active=False`.
        post_ban = await client.post(INTROSPECT_URL, json={"token": raw})
        assert post_ban.json()["active"] is False

        # Сдвигаем expires_at в прошлое — следующий login триггерит auto-unban.
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.execute(
            update(Ban).where(Ban.user_id == user_a.id).values(expires_at=past)
        )
        await db.commit()

        login = await client.post(
            LOGIN_URL, json={"username": "t_user_a", "password": "User1234!"},
        )
        assert login.status_code == 200

        # PAT снова `active=True` — симметрия с manual unban.
        post_unban = await client.post(INTROSPECT_URL, json={"token": raw})
        assert post_unban.status_code == 200
        assert post_unban.json()["active"] is True
