"""Failure-audit emits, lazy re-encrypt commit, account_admin emergency transfer/recover.

Покрытие:

* `tokens.create / failure` эмитится при ConflictError NAME_DUPLICATE.
* `tokens.transfer_ownership / failure` эмитится при denied (не admin своего dept'а).
* `tokens.delete / failure` эмитится на NotFound.
* `tokens.dept_grant_added / failure` эмитится на DuplicateError.
* `tokens.role_acl_added / failure` эмитится на denied / DomainValidationError.
* Lazy re-encrypt через `reveal()` коммитит CAS-UPDATE в БД (а не откатывает
  при close-сессии). Тест использует реальную AsyncSession БЕЗ
  SAVEPOINT-обёртки, чтобы убедиться: после `reveal()` строка действительно
  лежит под новой версией.
* `transfer`/`recover` для account_admin — emergency-override (проходит для
  cred'ов с удалённым отделом-владельцем); обычный CRUD/reveal ему закрыт.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
)
from src.dependencies.auth import Identity
from src.repositories import credentials as cred_repo
from src.repositories import dept_grants as grants_repo
from src.schemas.credentials import (
    CredentialCreate,
    TransferRequest,
)
from src.schemas.dept_grants import DeptGrantCreate
from src.schemas.role_acls import RoleACLCreate
from src.services import (
    credential_service,
    dept_grant_service,
    role_acl_service,
    secrets_service,
)
from tests._helpers import b64


# ── helpers ─────────────────────────────────────────────────────────────────


def _operator(user_id: str = "usr_op0000000000000000000000001", department_id: str = "dep_op000000000000000000000001") -> Identity:
    return Identity(
        user_id=user_id,
        username="op",
        actor_type="user",
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["operator"]},
        is_banned=False,
        platform_role=None,
    )


def _admin(user_id: str = "usr_svc_admin0000000000000001", department_id: str = "dep_admin000000000000000001") -> Identity:
    return Identity(
        user_id=user_id,
        username="adm",
        actor_type="user",
        department_id=department_id,
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["admin"]},
        is_banned=False,
        platform_role=None,
    )


def _account_admin(user_id: str = "usr_acc_admin0000000000000001") -> Identity:
    return Identity(
        user_id=user_id,
        username="acc",
        actor_type="user",
        department_id=None,
        allowed_services=["secret_service"],
        service_roles={},
        is_banned=False,
        platform_role="account_admin",
    )


def _capture_emits(svc_module):
    """Перехват audit-эмитов модуля сервиса — возвращает (patcher, list).

    Все три сервиса импортируют `from src.services import audit_service` и
    дёргают `audit_service.emit(...)`. Патчим именно референс в модуле сервиса,
    иначе перехват не сработает.
    """
    emitted: list[tuple[str, dict]] = []
    p = patch.object(
        svc_module.audit_service,
        "emit",
        side_effect=lambda action, **kw: emitted.append((action, kw)),
    )
    return p, emitted


def _failures(emitted, action: str) -> list[dict]:
    return [kw for a, kw in emitted if a == action and kw.get("status") == "failure"]


# ── tokens.create / failure ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_emits_failure_on_name_duplicate(adb) -> None:
    """Дубликат активной cred → ConflictError + tokens.create/failure."""
    actor = _operator(user_id="usr_creat_dup00000000000000001")
    payload = CredentialCreate(
        name="dup_test",
        service="jira",
        scope="personal",
        secret_b64=b64("x"),
    )
    patcher, emitted = _capture_emits(credential_service)
    with patcher:
        await credential_service.create(adb, actor, payload)
        with pytest.raises(ConflictError) as exc_info:
            await credential_service.create(adb, actor, payload)

    assert exc_info.value.error_code == "NAME_DUPLICATE"
    failures = _failures(emitted, "tokens.create")
    assert len(failures) == 1, f"expected 1 failure, got {emitted}"
    details = failures[0]["details"]
    assert details["error_code"] == "NAME_DUPLICATE"
    assert details["scope"] == "personal"
    assert details["service"] == "jira"


@pytest.mark.asyncio
async def test_create_emits_failure_on_cross_dep_denied(adb) -> None:
    """Operator пробует завести cred на чужой dep → AuthorizationError + failure."""
    actor = _operator(user_id="usr_creat_xdep0000000000000001", department_id="dep_mine0000000000000000000001")
    payload = CredentialCreate(
        name="xdep_test",
        service="jira",
        scope="department",
        secret_b64=b64("x"),
        owner_dept_id="dep_other00000000000000000001",
    )
    patcher, emitted = _capture_emits(credential_service)
    with patcher:
        with pytest.raises(AuthorizationError) as exc_info:
            await credential_service.create(adb, actor, payload)

    assert exc_info.value.error_code == "CREDENTIAL_ACCESS_DENIED"
    failures = _failures(emitted, "tokens.create")
    assert len(failures) == 1
    assert failures[0]["details"]["error_code"] == "CREDENTIAL_ACCESS_DENIED"


# ── tokens.transfer_ownership / failure ────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_emits_failure_on_not_found(adb) -> None:
    admin = _admin()
    patcher, emitted = _capture_emits(credential_service)
    with patcher:
        with pytest.raises(NotFoundError):
            await credential_service.transfer(
                adb, admin, "cred_nope000000000000000000001",
                TransferRequest(new_owner_user_id="usr_new000000000000000000001", reason="x"),
            )
    failures = _failures(emitted, "tokens.transfer_ownership")
    assert len(failures) == 1
    assert failures[0]["details"]["error_code"] == "CREDENTIAL_NOT_FOUND"


@pytest.mark.asyncio
async def test_transfer_emits_failure_on_denied_not_owner_admin(adb) -> None:
    """admin dep_B пробует transfer cred dep_A → AuthorizationError + failure."""
    cred = await cred_repo.create(
        adb,
        id="cred_trden0000000000000000001",
        name="trden",
        service="jira",
        scope="personal",
        owner_user_id="usr_owner_trden00000000000001",
        owner_dept_id=None,
        owner_user_dept_id="dep_A_trden0000000000000001",
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_user_deleted",
        created_by="usr_owner_trden00000000000001",
    )
    await adb.commit()

    foreign_admin = _admin(
        user_id="usr_admin_dep_B000000000000001",
        department_id="dep_B_trden0000000000000001",
    )
    patcher, emitted = _capture_emits(credential_service)
    with patcher:
        with pytest.raises(AuthorizationError):
            await credential_service.transfer(
                adb, foreign_admin, cred.id,
                TransferRequest(new_owner_user_id="usr_new000000000000000000001", reason="x"),
            )
    failures = _failures(emitted, "tokens.transfer_ownership")
    assert len(failures) == 1
    assert failures[0]["details"]["error_code"] == "CREDENTIAL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_transfer_account_admin_emergency_allowed(adb) -> None:
    """account_admin может emergency-transfer cred'а с удалённым отделом-владельцем."""
    cred = await cred_repo.create(
        adb,
        id="cred_aa_tr0000000000000000001",
        name="aa_tr",
        service="git",
        scope="cross_department",
        owner_user_id=None,
        owner_dept_id="dep_dead00000000000000000001",
        owner_user_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_dept_deleted",
        created_by="usr_creator0000000000000000001",
    )
    await adb.commit()

    aa = _account_admin()
    patcher, emitted = _capture_emits(credential_service)
    with patcher:
        await credential_service.transfer(
            adb, aa, cred.id,
            TransferRequest(new_owner_dept_id="dep_new00000000000000000000001", reason="dissolved"),
        )
    # emergency-transfer проходит: failure-эмитов нет, владелец переназначен
    assert _failures(emitted, "tokens.transfer_ownership") == []
    refreshed = await cred_repo.get_by_id(adb, cred.id)
    assert refreshed.owner_dept_id == "dep_new00000000000000000000001"


# ── tokens.recover (account_admin emergency override) ───────────────────────


@pytest.mark.asyncio
async def test_recover_account_admin_emergency_allowed(adb) -> None:
    """account_admin может emergency-recover blocked-cred."""
    cred = await cred_repo.create(
        adb,
        id="cred_aa_rc0000000000000000001",
        name="aa_rc",
        service="git",
        scope="department",
        owner_user_id=None,
        owner_dept_id="dep_owner_rc000000000000001",
        owner_user_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="blocked",
        blocked_reason="owner_user_deleted",
        created_by="usr_someone000000000000000001",
    )
    await adb.commit()

    aa = _account_admin()
    patcher, emitted = _capture_emits(credential_service)
    with patcher:
        await credential_service.recover(adb, aa, cred.id)
    # emergency-recover проходит: failure-эмитов нет, cred разблокирован
    assert _failures(emitted, "tokens.recover") == []
    refreshed = await cred_repo.get_by_id(adb, cred.id)
    assert refreshed.status == "active"


# ── tokens.delete / failure ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_emits_failure_on_not_found(adb) -> None:
    actor = _operator()
    patcher, emitted = _capture_emits(credential_service)
    with patcher:
        with pytest.raises(NotFoundError):
            await credential_service.delete(adb, actor, "cred_nopedel000000000000000001", None)
    failures = _failures(emitted, "tokens.delete")
    assert len(failures) == 1
    assert failures[0]["details"]["error_code"] == "CREDENTIAL_NOT_FOUND"


# ── tokens.dept_grant_added / failure ──────────────────────────────────────


@pytest.mark.asyncio
async def test_dept_grant_add_emits_failure_on_recipient_is_owner(adb) -> None:
    """recipient_dept_id == owner_dept_id → DomainValidationError + failure."""
    owner_dep = "dep_owner_grerr00000000000001"
    cred = await cred_repo.create(
        adb,
        id="cred_grerr00000000000000000001",
        name="grerr",
        service="git",
        scope="cross_department",
        owner_user_id=None,
        owner_dept_id=owner_dep,
        owner_user_dept_id=None,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_admin_grerr0000000000000001",
    )
    await adb.commit()

    # dep_admin owner_dep'а имеет grant_dept на свою cross_dep cred.
    dep_admin = Identity(
        user_id="usr_admin_grerr0000000000000001",
        username="adm",
        actor_type="user",
        department_id=owner_dep,
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["admin"]},
        is_banned=False,
        platform_role="department_admin",
    )
    patcher, emitted = _capture_emits(dept_grant_service)
    from src.core.exceptions import DomainValidationError

    with patcher:
        with pytest.raises(DomainValidationError):
            await dept_grant_service.add(
                adb,
                dep_admin,
                cred.id,
                DeptGrantCreate(recipient_dept_id=owner_dep),
            )
    failures = _failures(emitted, "tokens.dept_grant_added")
    assert len(failures) == 1
    assert failures[0]["details"]["error_code"] == "DEPT_GRANT_RECIPIENT_IS_OWNER"


# ── tokens.role_acl_added / failure ────────────────────────────────────────


@pytest.mark.asyncio
async def test_role_acl_add_emits_failure_on_personal_owner_dept_mismatch(adb) -> None:
    """ACL на personal не в dep'е владельца → DomainValidationError + failure."""
    owner_dep = "dep_owner_aclerr0000000000001"
    cred = await cred_repo.create(
        adb,
        id="cred_aclerr0000000000000000001",
        name="aclerr",
        service="jira",
        scope="personal",
        owner_user_id="usr_owner_aclerr00000000000001",
        owner_dept_id=None,
        owner_user_dept_id=owner_dep,
        login=None,
        secret_encrypted="v2$nonce$ct",
        status="active",
        created_by="usr_owner_aclerr00000000000001",
    )
    await adb.commit()

    owner = Identity(
        user_id="usr_owner_aclerr00000000000001",
        username="o",
        actor_type="user",
        department_id=owner_dep,
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["operator"]},
        is_banned=False,
        platform_role=None,
    )
    from src.core.exceptions import DomainValidationError

    patcher, emitted = _capture_emits(role_acl_service)
    with patcher:
        with pytest.raises(DomainValidationError):
            await role_acl_service.add(
                adb, owner, cred.id,
                RoleACLCreate(
                    dept_id="dep_someother00000000000000001",
                    role_name="reader",
                    can_read=True,
                ),
            )
    failures = _failures(emitted, "tokens.role_acl_added")
    assert len(failures) == 1
    assert failures[0]["details"]["error_code"] == "PERSONAL_ACL_OWNER_DEPT_ONLY"


# ── Lazy re-encrypt: реальный commit ───────────────────────────────────────


@pytest_asyncio.fixture()
async def real_session_factory():
    """Прямая AsyncSession БЕЗ SAVEPOINT-обёртки.

    Нужна для теста, который проверяет: lazy re-encrypt commit'ит CAS-UPDATE
    в БД. SAVEPOINT-fixture (`adb`) откатывает всё на teardown, поэтому
    отличить «commit прошёл» от «commit не прошёл, но в txn видно» там нельзя.
    Здесь — отдельный engine, явный commit, явная чистка строки в конце.
    """
    dsn = os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg://app_user:app_password@test-postgres:5432/secret_db_test",
        ),
    )
    engine = create_async_engine(dsn, pool_pre_ping=True)

    created_ids: list[str] = []

    def _factory() -> AsyncSession:
        return AsyncSession(engine, expire_on_commit=False)

    yield _factory, created_ids

    # Cleanup: удалить все созданные строки через свежую сессию.
    if created_ids:
        async with engine.connect() as conn:
            await conn.execute(
                text("DELETE FROM credentials WHERE id = ANY(:ids)"),
                {"ids": created_ids},
            )
            await conn.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reveal_lazy_reencrypt_actually_commits(real_session_factory, monkeypatch) -> None:
    """E2E: создать v2-cred, поднять активную версию до v3, выполнить reveal,
    закрыть сессию, открыть новую — строка должна лежать под v3$ (а не v2$).

    До фикса CAS-UPDATE откатывался на close-сессии — каждый reveal делал
    «миграцию», но БД её не видела, и `--finalize` master-key никогда не
    сходился."""
    cred_id = f"cred_lazycmt_{uuid.uuid4().hex[:8]}"
    aad = secrets_service.aad_for_credential(cred_id)
    token_v2 = secrets_service.encrypt("commit-secret", aad=aad)
    assert token_v2.startswith("v2$")

    factory, created_ids = real_session_factory
    created_ids.append(cred_id)

    owner_id = "usr_lazycmt_owner0000000000001"

    # Sessoin #1 — посев (без SAVEPOINT, реальный commit).
    async with factory() as s1:
        await cred_repo.create(
            s1,
            id=cred_id,
            name=f"lazycmt_{cred_id[-4:]}",
            service="jira",
            scope="personal",
            owner_user_id=owner_id,
            owner_dept_id=None,
            owner_user_dept_id="dep_lazycmt0000000000000000001",
            login="alice",
            secret_encrypted=token_v2,
            status="active",
            created_by=owner_id,
        )
        await s1.commit()

    # Bump активной версии до v3 (legacy v2 остаётся доступной для decrypt'а).
    from src.core.config import get_settings

    monkeypatch.setenv(
        "SECRET_ENCRYPTION_KEY__v2",
        os.environ["SECRET_ENCRYPTION_KEY"],
    )
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "3")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    # Session #2 — reveal'им через сервис (он сам коммитит CAS-UPDATE).
    identity = Identity(
        user_id=owner_id,
        username="alice",
        actor_type="user",
        department_id="dep_lazycmt0000000000000000001",
        allowed_services=["secret_service"],
        service_roles={"secret_service": ["reader"]},
        is_banned=False,
        platform_role=None,
    )
    # Чтобы audit-эмит не плевался во внешний loging — мокаем emit.
    with patch.object(credential_service.audit_service, "emit", lambda *a, **k: None):
        async with factory() as s2:
            login, secret_b64 = await credential_service.reveal(s2, identity, cred_id)
            assert login == "alice"

    # Session #3 — свежая сессия, без shared state со s2.
    async with factory() as s3:
        fresh = await cred_repo.get_by_id(s3, cred_id)
        assert fresh is not None
        assert fresh.secret_encrypted.startswith("v3$"), (
            f"lazy re-encrypt не закоммитился: blob={fresh.secret_encrypted[:8]!r}"
        )

    get_settings.cache_clear()  # type: ignore[attr-defined]
