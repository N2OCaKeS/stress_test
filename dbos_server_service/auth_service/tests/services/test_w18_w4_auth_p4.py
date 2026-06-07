"""auth: ещё один набор carry-фиксов (group_service GET, redaction, oauth).

Точки:
  1. group_service GET endpoints (list_members / list_bot_members /
     list_group_services / list_group_roles) — DA своего отдела теперь имеет
     доступ; cross-dept DA / обычный юзер по-прежнему 403.
  3. redaction._HASH_KEYS больше не содержит generic `hash` — git_commit_hash
     и подобные не маскируются.
  4. redaction._SECRET_KEYS содержит `service_api_key` и `service_key`.
  5. oauth_service.issue_authorization_code — confidential клиент с
     PKCE=plain пишет WARNING.
  7. dependencies/auth: invalidate_identity_cache_for_user через обратный
     индекс — O(K); попутно eviction по maxsize чистит индекс.
  9. user_service.get_user_permissions — упрощённое условие dept-admin
     ветки (sanity: cross-dept по-прежнему 403, self/admin не падает).
  10. oauth_service.exchange_code → OAUTH_USER_NOT_FOUND при гонке
      DELETE user.
  12. cov gaps:
     - _resolve_actor_dept(actor_id=None) → None ветка.
     - collect_user_permissions(include_groups=False) → пустой dict groups.
     - reset_password actor_role=None + DA + cross-dept → 403.
     - issue_authorization_code confidential + unknown PKCE method → 422.

Item 2 (`if client.department_id else []`) и item 13 (test_narrowing) уже
закрыты в коде ветки — отдельные тесты не нужны (там и так зелёные).
Item 6 (per-service rate-limit) отложен — slowapi single-key_func и под
наш monkeypatch-фреймворк ввести custom key без рефакторинга не выходит.
Item 8 (failure-audit invariant) — только docstring, тестировать нечего
сверх уже имеющегося `test_update_bot_cross_tenant_emits_failure_audit`.
Item 11 (_dummy_password_hash invariant) — только docstring.
"""

import logging

import pytest

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError
from src.core.security import hash_opaque_token
from src.models import UserGroup
from src.schemas.auth import IdentityContext
from src.services import (
    auth_service,
    bot_service,
    group_service,
    oauth_service,
    redaction,
    user_service,
)
from src.utils.ids import _new_id


GROUPS_URL = "/api/auth/v1/groups"


# ── helpers ────────────────────────────────────────────────────────────────

def _make_identity(
    *,
    user_id: str,
    department_id: str | None = None,
    platform_role: PlatformRole | None = None,
) -> IdentityContext:
    return IdentityContext(
        user_id=user_id,
        username=user_id,
        department_id=department_id,
        platform_role=platform_role,
    )


async def _make_group(db, dept_id: str, name: str = "p4_group") -> UserGroup:
    grp = UserGroup(
        id=_new_id("grp_"),
        department_id=dept_id,
        name=name,
        display_name=name.title(),
        description=None,
        is_active=True,
    )
    db.add(grp)
    await db.flush()
    return grp


# ── 1. group_service GET endpoints: DA-of-own-dept доступ ──────────────────

class TestGroupGetDeptAdminAccess:
    async def test_list_members_dept_admin_own_dept_ok(
        self, db, dept_admin_a, dept_a_with_service,
    ):
        grp = await _make_group(db, dept_a_with_service.id)
        identity = _make_identity(
            user_id=dept_admin_a.id,
            department_id=dept_a_with_service.id,
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
        )
        result = await group_service.list_members(db, identity, grp.id)
        assert result == []

    async def test_list_members_dept_admin_cross_dept_denied(
        self, db, dept_admin_b, dept_a_with_service,
    ):
        grp = await _make_group(db, dept_a_with_service.id, name="cross_dept_grp")
        identity = _make_identity(
            user_id=dept_admin_b.id,
            department_id="dept_other",
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
        )
        with pytest.raises(AuthorizationError) as ei:
            await group_service.list_members(db, identity, grp.id)
        assert ei.value.error_code == "DEPARTMENT_ACCESS_DENIED"

    async def test_list_members_regular_user_denied(
        self, db, user_a, dept_a_with_service,
    ):
        grp = await _make_group(db, dept_a_with_service.id, name="reg_user_grp")
        identity = _make_identity(
            user_id=user_a.id,
            department_id=dept_a_with_service.id,
            platform_role=None,
        )
        with pytest.raises(AuthorizationError) as ei:
            await group_service.list_members(db, identity, grp.id)
        assert ei.value.error_code == "ROLE_REQUIRED"

    async def test_list_bot_members_dept_admin_own_dept_ok(
        self, db, dept_admin_a, dept_a_with_service,
    ):
        grp = await _make_group(db, dept_a_with_service.id, name="botmembers_grp")
        identity = _make_identity(
            user_id=dept_admin_a.id,
            department_id=dept_a_with_service.id,
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
        )
        assert await group_service.list_bot_members(db, identity, grp.id) == []

    async def test_list_group_services_dept_admin_own_dept_ok(
        self, db, dept_admin_a, dept_a_with_service,
    ):
        grp = await _make_group(db, dept_a_with_service.id, name="svc_grp")
        identity = _make_identity(
            user_id=dept_admin_a.id,
            department_id=dept_a_with_service.id,
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
        )
        assert await group_service.list_group_services(db, identity, grp.id) == []

    async def test_list_group_services_dept_admin_cross_dept_denied(
        self, db, dept_admin_b, dept_a_with_service,
    ):
        grp = await _make_group(db, dept_a_with_service.id, name="cross_svc_grp")
        identity = _make_identity(
            user_id=dept_admin_b.id,
            department_id="dept_other",
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
        )
        with pytest.raises(AuthorizationError) as ei:
            await group_service.list_group_services(db, identity, grp.id)
        assert ei.value.error_code == "DEPARTMENT_ACCESS_DENIED"

    async def test_list_group_roles_dept_admin_own_dept_ok(
        self, db, dept_admin_a, dept_a_with_service,
    ):
        grp = await _make_group(db, dept_a_with_service.id, name="roles_grp")
        identity = _make_identity(
            user_id=dept_admin_a.id,
            department_id=dept_a_with_service.id,
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
        )
        assert await group_service.list_group_roles(db, identity, grp.id) == []

    async def test_list_group_roles_account_admin_any_dept_ok(
        self, db, account_admin, dept_a_with_service,
    ):
        grp = await _make_group(db, dept_a_with_service.id, name="acct_roles_grp")
        identity = _make_identity(
            user_id=account_admin.id,
            department_id=None,
            platform_role=PlatformRole.ACCOUNT_ADMIN,
        )
        assert await group_service.list_group_roles(db, identity, grp.id) == []


# ── 3. redaction: generic `hash` больше не маскируется ─────────────────────

class TestRedactionHashKeysGenericRemoved:
    def test_generic_hash_key_passes_through(self):
        # git_commit_hash — легитимная не-секретная ID-строка; должна остаться.
        result = redaction.redact({"git_commit_hash": "deadbeefcafebabe1234"})
        assert result == {"git_commit_hash": "deadbeefcafebabe1234"}

    def test_password_hash_still_masked(self):
        # Конкретные хэш-ключи остались в множестве.
        result = redaction.redact({"password_hash": "$argon2id$v=19$..."})
        assert result == {"password_hash": "<HASH>"}

    def test_pat_hash_masked(self):
        result = redaction.redact({"pat_hash": "abc123abc"})
        assert result == {"pat_hash": "<HASH>"}

    def test_bot_token_hash_masked(self):
        result = redaction.redact({"bot_token_hash": "abc"})
        # Может быть классифицирован как <TOKEN> или <HASH> — оба варианта
        # маскируют, главное что не plaintext.
        assert result["bot_token_hash"] in {"<TOKEN>", "<HASH>"}


# ── 4. redaction: service_api_key / service_key в _SECRET_KEYS ─────────────

class TestRedactionServiceKeys:
    def test_service_api_key_masked(self):
        result = redaction.redact({"service_api_key": "raw-key-value"})
        assert result == {"service_api_key": "<SECRET>"}

    def test_service_key_masked(self):
        result = redaction.redact({"service_key": "raw-key-value"})
        assert result == {"service_key": "<SECRET>"}


# ── 5. oauth_service PKCE plain WARNING для confidential ────────────────────

class TestOAuthPkcePlainWarning:
    async def test_confidential_with_pkce_plain_warns(
        self, db, account_admin, dept_a, user_a, caplog,
    ):
        from src.repositories.oauth_clients import OAuthClientRepository

        repo = OAuthClientRepository(db)
        client = await repo.create(
            department_id=dept_a.id,
            name="conf_plain_app",
            client_secret_hash=hash_opaque_token("cs_dummy_secret_for_test_xyz"),
            client_secret_prefix="cs_dummy_secre",
            redirect_uris=["https://app/cb"],
            allowed_scopes=[],
            grant_types=["authorization_code"],
            is_public=False,
            created_by=account_admin.id,
        )
        await db.commit()

        with caplog.at_level(logging.WARNING, logger="src.services.oauth_service"):
            await oauth_service.issue_authorization_code(
                db,
                client_id=client.client_id,
                user_id=user_a.id,
                redirect_uri="https://app/cb",
                scopes=[],
                code_challenge="challenge_string_long_enough",
                code_challenge_method="plain",
            )

        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "PKCE plain" in r.getMessage()
        ]
        assert warning_records, f"ожидалось WARNING про PKCE plain, есть: {caplog.records}"
        assert client.client_id in warning_records[0].getMessage()

    async def test_confidential_with_pkce_s256_no_warning(
        self, db, account_admin, dept_a, user_a, caplog,
    ):
        from src.repositories.oauth_clients import OAuthClientRepository

        repo = OAuthClientRepository(db)
        client = await repo.create(
            department_id=dept_a.id,
            name="conf_s256_app",
            client_secret_hash=hash_opaque_token("cs_dummy_secret_for_test_xyz"),
            client_secret_prefix="cs_dummy_secre",
            redirect_uris=["https://app/cb"],
            allowed_scopes=[],
            grant_types=["authorization_code"],
            is_public=False,
            created_by=account_admin.id,
        )
        await db.commit()

        with caplog.at_level(logging.WARNING, logger="src.services.oauth_service"):
            await oauth_service.issue_authorization_code(
                db,
                client_id=client.client_id,
                user_id=user_a.id,
                redirect_uri="https://app/cb",
                scopes=[],
                code_challenge="challenge_string_long_enough",
                code_challenge_method="S256",
            )

        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "PKCE plain" in r.getMessage()
        ]
        assert not warning_records, "S256 не должен триггерить PKCE-plain WARNING"


# ── 10. exchange_code: OAUTH_USER_NOT_FOUND на race с DELETE user ──────────

class TestExchangeCodeUserNotFound:
    # Заранее сгенерированный PKCE pair (S256): verifier ↔ challenge.
    _VERIFIER = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"
    _CHALLENGE = "YHBP6B6mvodQJzAPdYIxjkKX-y4g-eurpel--I4hSGU"

    async def test_user_deleted_between_authorize_and_exchange(
        self, db, account_admin, dept_a, monkeypatch,
    ):
        from src.repositories.oauth_clients import OAuthClientRepository

        repo = OAuthClientRepository(db)
        # Public-клиент: identity по PKCE-verifier'у, client_secret пустой.
        client = await repo.create(
            department_id=dept_a.id,
            name="public_user_race_app",
            client_secret_hash="",
            client_secret_prefix="",
            redirect_uris=["https://app/cb"],
            allowed_scopes=[],
            grant_types=["authorization_code"],
            is_public=True,
            created_by=account_admin.id,
        )
        await db.commit()

        raw_code = await oauth_service.issue_authorization_code(
            db,
            client_id=client.client_id,
            user_id=account_admin.id,
            redirect_uri="https://app/cb",
            scopes=[],
            code_challenge=self._CHALLENGE,
            code_challenge_method="S256",
        )

        # Симулируем гонку: user_repo.get_by_id во время exchange отдаёт None.
        from src.repositories import users as users_repo_mod

        async def _none_get(self, user_id):
            return None

        monkeypatch.setattr(users_repo_mod.UserRepository, "get_by_id", _none_get)

        from src.core.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError) as ei:
            await oauth_service.exchange_code(
                db,
                client_id=client.client_id,
                client_secret="",
                code=raw_code,
                redirect_uri="https://app/cb",
                code_verifier=self._VERIFIER,
            )
        assert ei.value.error_code == "OAUTH_USER_NOT_FOUND"


# ── 12a. _resolve_actor_dept(actor_id=None) → None ─────────────────────────

class TestResolveActorDeptNone:
    async def test_actor_id_none_returns_passed_dept(self, db):
        # actor_id=None и actor_department_id=None → возвращает None без
        # SELECT'а (ранний выход).
        result = await bot_service._resolve_actor_dept(db, None, None)
        assert result is None

    async def test_actor_id_none_returns_explicit_dept(self, db):
        # actor_id=None, но caller передал dept → отдаём его как есть.
        result = await bot_service._resolve_actor_dept(db, None, "dept_x")
        assert result == "dept_x"


# ── 12b. collect_user_permissions(include_groups=False) ────────────────────

class TestCollectUserPermissionsNoGroups:
    async def test_include_groups_false_returns_empty_dict(
        self, db, user_a,
    ):
        allowed, roles, groups = await auth_service.collect_user_permissions(
            db, user_a, include_groups=False,
        )
        # groups всегда пустой dict при include_groups=False, независимо от
        # реального членства.
        assert groups == {}
        # allowed/roles при этом валидны.
        assert isinstance(allowed, list)
        assert isinstance(roles, dict)


# ── 12c. reset_password actor_role=None + DA + cross-dept ──────────────────

class TestResetPasswordActorRoleNoneCrossDept:
    async def test_dept_admin_resolved_from_db_blocks_cross_dept(
        self, db, dept_admin_b, user_a,
    ):
        # actor_role=None → reset_password подтянет platform_role из БД,
        # обнаружит department_admin, и cross-dept проверка отобьёт 403.
        with pytest.raises(AuthorizationError) as ei:
            await user_service.reset_password(
                db,
                actor_id=dept_admin_b.id,
                user_id=user_a.id,
                new_password="NewPass1234!",
                actor_role=None,
            )
        assert ei.value.error_code == "USER_RESET_PASSWORD_FORBIDDEN"


# ── 12d. issue_authorization_code: unknown PKCE method для confidential ─────

class TestIssueAuthCodeUnknownPkceMethod:
    async def test_confidential_unknown_method_rejected(
        self, db, account_admin, dept_a, user_a,
    ):
        from src.repositories.oauth_clients import OAuthClientRepository

        repo = OAuthClientRepository(db)
        client = await repo.create(
            department_id=dept_a.id,
            name="conf_unknown_pkce_app",
            client_secret_hash=hash_opaque_token("cs_dummy_secret_for_test_xyz"),
            client_secret_prefix="cs_dummy_secre",
            redirect_uris=["https://app/cb"],
            allowed_scopes=[],
            grant_types=["authorization_code"],
            is_public=False,
            created_by=account_admin.id,
        )
        await db.commit()

        with pytest.raises(AuthorizationError) as ei:
            await oauth_service.issue_authorization_code(
                db,
                client_id=client.client_id,
                user_id=user_a.id,
                redirect_uri="https://app/cb",
                scopes=[],
                code_challenge="challenge_string_long_enough",
                code_challenge_method="MD5",
            )
        assert ei.value.error_code == "PKCE_METHOD_INVALID"
