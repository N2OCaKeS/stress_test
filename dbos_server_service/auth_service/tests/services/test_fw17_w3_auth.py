"""P4 кластер auth: charset-валидация имени сервиса/роли, fail-closed для
read-операций OAuth-клиентов, дедуп legacy-ключа в bot.list-audit и маска
PII-email'а в user.create/user.update.

Зоны:
  1. `ServiceCreate.service_name` / `ServiceRoleCreate.role_name` — pattern
     `^[a-z][a-z0-9_]+$`. Точка ломала `i.split('.', 1)` в PAT-scope-фильтре,
     заглавные/digit-prefix расходятся с конвенцией snake_case.
  2. `oauth_service.list_clients` для DEPARTMENT_ADMIN с актором=None раньше
     возвращал `[]` (silent-empty), теперь — `AuthorizationError(ACTOR_VANISHED)`,
     симметрично `create_client`/`delete_client` через
     `_dept_guard.assert_actor_exists`.
  3. `bot_service.list_bots` audit-details — убран дубль `filter_department_id`,
     остались только канонические `filter_department_id_requested` и
     `filter_department_id_effective`.
  4. `user.create` / `user.update` — email в audit-details (для user.create в
     корне, для user.update внутри `changes`) теперь маскируется через
     `mask_email`; в БД пишется полный email.
"""

import pytest

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError
from src.core.security import mask_email
from src.repositories.users import UserRepository
from src.schemas.services import ServiceCreate
from src.schemas.service_roles import ServiceRoleCreate
from src.services import audit_service as audit_mod
from src.services import bot_service, oauth_service, user_service


# ── 1. Pattern для service_name / role_name ──────────────────────────────────


class TestServiceCreatePattern:
    @pytest.mark.parametrize("bad", ["docker.registry", "Foo", "123_svc", "x", "with space", "with-dash"])
    def test_service_name_rejected(self, bad):
        with pytest.raises(Exception):
            ServiceCreate(service_name=bad, display_name="x")

    @pytest.mark.parametrize("ok", ["server_service", "loging_service", "config_service", "x1", "a_b_c"])
    def test_service_name_accepted(self, ok):
        m = ServiceCreate(service_name=ok, display_name="x")
        assert m.service_name == ok


class TestServiceRoleCreatePattern:
    @pytest.mark.parametrize("bad", ["admin.rw", "Reader", "1role", " role", "role-name", "r"])
    def test_role_name_rejected(self, bad):
        with pytest.raises(Exception):
            ServiceRoleCreate(role_name=bad, display_name="x")

    @pytest.mark.parametrize("ok", ["admin", "reader", "operator", "guest", "worker_bot", "ro1"])
    def test_role_name_accepted(self, ok):
        m = ServiceRoleCreate(role_name=ok, display_name="x")
        assert m.role_name == ok

    def test_seed_dev_role_names_pass(self):
        """Все role_name из scripts/seed_dev.py должны пройти pattern."""
        for rn in ("reader", "operator", "admin", "worker_bot", "guest"):
            ServiceRoleCreate(role_name=rn, display_name="x")


# ── 2. list_clients: actor=None → ACTOR_VANISHED ─────────────────────────────


class TestOAuthListClientsActorVanished:
    async def test_dept_admin_actor_vanished_raises(self, db, dept_admin_a, monkeypatch):
        """DA с актором, исчезнувшим между issue JWT и call'ом → 403
        ACTOR_VANISHED, не silent-empty."""
        original = UserRepository.get_by_id

        async def patched(self, uid):
            if uid == dept_admin_a.id:
                return None
            return await original(self, uid)

        monkeypatch.setattr(UserRepository, "get_by_id", patched)

        with pytest.raises(AuthorizationError) as ei:
            await oauth_service.list_clients(
                db,
                actor_id=dept_admin_a.id,
                actor_role=PlatformRole.DEPARTMENT_ADMIN,
            )
        assert ei.value.error_code == "ACTOR_VANISHED"

    async def test_account_admin_path_unaffected(self, db, account_admin):
        """account_admin: actor_role != DEPARTMENT_ADMIN, guard не дёргается,
        пустой список — норма (нет OAuth-клиентов в свежей БД)."""
        result = await oauth_service.list_clients(
            db,
            actor_id=account_admin.id,
            actor_role=PlatformRole.ACCOUNT_ADMIN,
        )
        assert result == []

    async def test_dept_admin_happy_path(self, db, dept_admin_a):
        """DA с живым актором: возвращается список клиентов своего отдела
        (пустой в этой фикстуре)."""
        result = await oauth_service.list_clients(
            db,
            actor_id=dept_admin_a.id,
            actor_role=PlatformRole.DEPARTMENT_ADMIN,
        )
        assert result == []


# ── 3. bot.list audit — без legacy-дубля ─────────────────────────────────────


def _capture_emits(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


class TestBotListAuditNoLegacyDup:
    async def test_audit_payload_has_no_filter_department_id_key(
        self, db, account_admin, monkeypatch,
    ):
        """В details `bot.list` должен остаться только канонический пара
        `filter_department_id_requested` / `filter_department_id_effective` —
        legacy-ключ `filter_department_id` удалён."""
        captured = _capture_emits(monkeypatch)

        await bot_service.list_bots(
            db,
            actor_id=account_admin.id,
            actor_role=PlatformRole.ACCOUNT_ADMIN,
            department_id=None,
        )

        events = [e for e in captured if e.get("action") == "bot.list"]
        assert events, captured
        details = events[-1].get("details") or {}
        assert "filter_department_id" not in details, (
            f"legacy ключ filter_department_id всё ещё в audit: {details}"
        )
        assert "filter_department_id_requested" in details
        assert "filter_department_id_effective" in details


# ── 4. mask_email helper + audit в user.create / user.update ─────────────────


class TestMaskEmailHelper:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("john.doe@corp.local", "j***@corp.local"),
            ("a@x", "a***@x"),
            ("USER@Example.COM", "U***@Example.COM"),
        ],
    )
    def test_basic_masking(self, raw, expected):
        assert mask_email(raw) == expected

    def test_none_and_empty(self):
        assert mask_email(None) is None
        assert mask_email("") == ""

    @pytest.mark.parametrize("bad", ["notanemail", "@nodomain", "no@", "@", "  "])
    def test_malformed_returns_placeholder(self, bad):
        # любой не-email возвращает `<EMAIL>` или сохраняется как пустота —
        # главное, plaintext не утекает обратно
        masked = mask_email(bad)
        assert masked == "<EMAIL>" or masked == bad and "@" not in bad


class TestUserCreateEmailMasked:
    async def test_create_user_audit_email_is_masked(
        self, db, account_admin, dept_a_with_service, monkeypatch,
    ):
        captured = _capture_emits(monkeypatch)
        email = "alice.smith@corp.local"

        await user_service.create_user(
            db,
            actor_id=account_admin.id,
            actor_role=PlatformRole.ACCOUNT_ADMIN,
            username="alice_pii_test",
            password="Alice1234!",
            department_id=dept_a_with_service.id,
            email=email,
        )

        events = [e for e in captured if e.get("action") == "user.create"]
        assert events, captured
        details = events[-1].get("details") or {}
        assert "email" in details
        assert details["email"] != email, (
            f"plaintext email утёк в audit-trail: {details['email']}"
        )
        assert details["email"] == "a***@corp.local"


class TestUserUpdateEmailMasked:
    async def test_update_user_audit_changes_email_masked(
        self, db, account_admin, user_a, monkeypatch,
    ):
        captured = _capture_emits(monkeypatch)
        new_email = "bob.brown@corp.local"

        await user_service.update_user(
            db,
            actor_id=account_admin.id,
            actor_role=PlatformRole.ACCOUNT_ADMIN,
            user_id=user_a.id,
            updates={"email": new_email},
        )

        events = [e for e in captured if e.get("action") == "user.update"]
        assert events, captured
        details = events[-1].get("details") or {}
        changes = details.get("changes") or {}
        assert "email" in changes
        assert changes["email"] != new_email, (
            f"plaintext email утёк в audit.changes: {changes['email']}"
        )
        assert changes["email"] == "b***@corp.local"
        # fields_changed остаётся неизменённым — диагностика «что менялось»
        # не теряется от маскировки значений.
        assert details.get("fields_changed") == ["email"]

    async def test_update_user_db_email_is_plaintext(
        self, db, account_admin, user_a,
    ):
        """В БД пишется полный email — маскировка только для audit."""
        new_email = "charlie@corp.local"

        await user_service.update_user(
            db,
            actor_id=account_admin.id,
            actor_role=PlatformRole.ACCOUNT_ADMIN,
            user_id=user_a.id,
            updates={"email": new_email},
        )

        await db.refresh(user_a)
        assert user_a.email == new_email
