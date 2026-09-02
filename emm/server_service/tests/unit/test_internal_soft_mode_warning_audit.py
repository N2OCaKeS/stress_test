"""Header-scoping internal-эндпоинтов на service-уровне.

`X-Target-Department-Id` — единственный cross-dept гард и enforce'ится
безусловно. Отдел самого воркер-бота в авторизации не участвует (один
глобальный бот обслуживает серверы всех отделов):

* заголовок отсутствует → 403 `TARGET_DEPARTMENT_HEADER_REQUIRED`,
  denied-audit с `reason=missing_target_department_header`;
* заголовок ≠ server.department_id → 404 (маска not-found),
  denied-audit с `reason=target_department_mismatch`;
* заголовок = server.department_id → операция проходит, даже если бот
  числится в другом отделе.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AuthorizationError, NotFoundError
from src.services import internal_service

from tests._helpers import make_emit_capture
from src.services.audit_events import SERVICE_EVENTS


def _stub_account_repo(monkeypatch, *, server_id: str, account_id: str, has_password: bool = True):
    """Заглушка для server_account repository — возвращает фейковый account."""
    target_server_id = server_id
    target_account_id = account_id
    pwd_state = "v1$nonce$cipher" if has_password else None

    class _Account:
        def __init__(self):
            self.id = target_account_id
            self.server_id = target_server_id
            self.login = "root"
            self.password_encrypted = pwd_state
            self.password_rotated_at = None

    async def get_by_id(db, aid):
        if aid == target_account_id:
            return _Account()
        return None

    async def update_password(db, account, encrypted):
        from datetime import datetime, timezone
        account.password_encrypted = encrypted
        account.password_rotated_at = datetime.now(timezone.utc)
        return account

    async def is_linked(db, aid, sid):
        # M2M: аккаунт привязан к серверу, если совпадают оба id.
        return aid == target_account_id and sid == target_server_id

    monkeypatch.setattr(internal_service.account_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(internal_service.account_repo, "update_password", update_password)
    monkeypatch.setattr(internal_service.account_repo, "is_linked", is_linked)


def _stub_permissions_ok(monkeypatch):
    async def require_action(db, identity, et, ac):
        return None

    monkeypatch.setattr(internal_service.permissions, "require_action", require_action)


def _stub_secrets(monkeypatch):
    from src.services.secrets_service import DecryptResult

    monkeypatch.setattr(
        internal_service.secrets_service,
        "decrypt",
        lambda c, **_kw: "plaintext",
    )
    monkeypatch.setattr(
        internal_service.secrets_service,
        "decrypt_with_meta",
        lambda c, **_kw: DecryptResult(
            plaintext="plaintext", source_version=1, needs_reencrypt=False,
        ),
    )
    monkeypatch.setattr(
        internal_service.secrets_service,
        "encrypt",
        lambda p, **_kw: "v1$nonce$cipher",
    )
    for attr in ("aad_for_server_account_password", "aad_for_ipmi_credential"):
        if hasattr(internal_service.secrets_service, attr):
            monkeypatch.setattr(
                internal_service.secrets_service, attr,
                lambda _id, _attr=attr: f"{_attr}:{_id}",
            )


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(monkeypatch)


def _identity(department_id: str = "dep_a"):
    from src.schemas.identity import IdentityContext

    return IdentityContext(
        user_id="bot_w",
        username="server_worker_user",
        department_id=department_id,
        service_roles={"server_service": ["worker_bot"]},
        subject_type="bot",
    )


def _stub_server_repo(monkeypatch, *, server_id: str, department_id: str | None = "dep_a"):
    """Заглушка для server repository — server lookup идёт до header-check'а."""
    class _Server:
        pass

    srv = _Server()
    srv.id = server_id
    srv.department_id = department_id

    async def get_by_id(db, sid):
        if sid == server_id:
            return srv
        return None

    monkeypatch.setattr(internal_service.server_repo, "get_by_id", get_by_id)


@pytest.mark.asyncio
async def test_fetch_account_password_no_header_returns_403(
    monkeypatch, captured_emits, db,
):
    """Отсутствие заголовка → 403 TARGET_DEPARTMENT_HEADER_REQUIRED + denied."""
    _stub_permissions_ok(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    with pytest.raises(AuthorizationError) as ei:
        await internal_service.fetch_account_password(
            db, _identity(), server_id="srv_1", account_id="acc_1",
            target_department_id=None,
        )
    assert ei.value.error_code == "TARGET_DEPARTMENT_HEADER_REQUIRED"

    denied = [
        e for e in captured_emits
        if e["action"] == "server_account.view_password" and e.get("status") == "denied"
    ]
    assert len(denied) == 1
    assert denied[0]["details"]["reason"] == "missing_target_department_header"


@pytest.mark.asyncio
async def test_fetch_account_password_header_mismatch_returns_404(
    monkeypatch, captured_emits, db,
):
    """Заголовок dep_b на сервере dep_a → 404 ACCOUNT_NOT_FOUND (маска) +
    denied `reason=target_department_mismatch`."""
    _stub_permissions_ok(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    with pytest.raises(NotFoundError) as ei:
        await internal_service.fetch_account_password(
            db, _identity(), server_id="srv_1", account_id="acc_1",
            target_department_id="dep_b",
        )
    assert ei.value.error_code == "ACCOUNT_NOT_FOUND"

    denied = [
        e for e in captured_emits
        if e["action"] == "server_account.view_password" and e.get("status") == "denied"
    ]
    assert len(denied) == 1
    assert denied[0]["details"]["reason"] == "target_department_mismatch"
    assert denied[0]["details"]["header_department_id"] == "dep_b"
    assert denied[0]["details"]["server_department_id"] == "dep_a"


@pytest.mark.asyncio
async def test_fetch_account_password_matched_header_succeeds(
    monkeypatch, captured_emits, db,
):
    """Совпавший заголовок → пароль отдаётся."""
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    result = await internal_service.fetch_account_password(
        db, _identity(), server_id="srv_1", account_id="acc_1",
        target_department_id="dep_a",
    )
    assert result["password"] == "plaintext"


@pytest.mark.asyncio
async def test_fetch_account_password_foreign_bot_matched_header_succeeds(
    monkeypatch, captured_emits, db,
):
    """Ключевой мультидепт-кейс: бот числится в dep_b, сервер — в dep_a,
    заголовок = dep_a → УСПЕХ. Отдел бота в авторизации не участвует."""
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    result = await internal_service.fetch_account_password(
        db, _identity(department_id="dep_b"), server_id="srv_1", account_id="acc_1",
        target_department_id="dep_a",
    )
    assert result["password"] == "plaintext"


@pytest.mark.asyncio
async def test_rotate_account_password_no_header_returns_403(
    monkeypatch, captured_emits, db,
):
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    with pytest.raises(AuthorizationError) as ei:
        await internal_service.rotate_account_password(
            db, _identity(), server_id="srv_1", account_id="acc_1",
            new_password="newpwd", target_department_id=None,
        )
    assert ei.value.error_code == "TARGET_DEPARTMENT_HEADER_REQUIRED"


@pytest.mark.asyncio
async def test_rotate_account_password_header_mismatch_returns_404(
    monkeypatch, captured_emits, db,
):
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    with pytest.raises(NotFoundError) as ei:
        await internal_service.rotate_account_password(
            db, _identity(), server_id="srv_1", account_id="acc_1",
            new_password="injected", target_department_id="dep_b",
        )
    assert ei.value.error_code == "ACCOUNT_NOT_FOUND"

    denied = [
        e for e in captured_emits
        if e["action"] == "server_account.rotate_password" and e.get("status") == "denied"
    ]
    assert len(denied) == 1
    assert denied[0]["details"]["reason"] == "target_department_mismatch"


@pytest.mark.asyncio
async def test_fetch_ipmi_credentials_header_mismatch_returns_404(
    monkeypatch, captured_emits, db,
):
    """fetch_ipmi_credentials: заголовок ≠ server dept → 404 SERVER_NOT_FOUND."""
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    class _Ctrl:
        id = "ctrl_1"
        kind = "redfish"
        endpoint_url = "https://1.2.3.4"
        username = "root"
        password_encrypted = "v1$nonce$cipher"

    async def get_by_server_id(db, sid):
        return _Ctrl()

    monkeypatch.setattr(internal_service.ipmi_repo, "get_by_server_id", get_by_server_id)

    with pytest.raises(NotFoundError) as ei:
        await internal_service.fetch_ipmi_credentials(
            db, _identity(), server_id="srv_1", target_department_id="dep_b",
        )
    assert ei.value.error_code == "SERVER_NOT_FOUND"

    denied = [
        e for e in captured_emits
        if e["action"] == "ipmi_controller.view_credentials" and e.get("status") == "denied"
    ]
    assert len(denied) == 1
    assert denied[0]["details"]["reason"] == "target_department_mismatch"


def test_platform_admin_blocked_description_matches_blocked_roles():
    """Description `http.platform_admin_blocked` упоминает ровно те роли,
    что действительно блокируются `BLOCKED_PLATFORM_ROLES`. loging_reader
    middleware'ом не блокируется — он не должен фигурировать в описании
    как блокируемый (раньше description ошибочно включал его)."""
    from src.middleware.platform_admin_guard import BLOCKED_PLATFORM_ROLES

    entry = next(
        e for e in SERVICE_EVENTS if e["action"] == "http.platform_admin_blocked"
    )
    desc = entry["description"]
    for role in BLOCKED_PLATFORM_ROLES:
        assert role in desc, (
            f"description must list {role} as blocked: {desc!r}"
        )
    # loging_reader не блокируется — он либо вообще не упомянут, либо
    # явно помечен как «NOT blocked». Запрещаем форму, где он перечислен
    # рядом с заблокированными ролями (как было до фикса).
    assert "account_admin/loging_admin/loging_reader" not in desc, (
        "loging_reader не должен числиться среди заблокированных в "
        "description события — middleware его пропускает"
    )
