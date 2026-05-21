"""Internal endpoints в soft-mode эмитят `internal.dept_header_missing` warning.

До фикса `fetch_account_password`/`rotate_account_password` пропускали server
lookup, если `X-Target-Department-Id` header отсутствует в soft-mode (экономия
на hot path). При этом `_check_target_department` НЕ вызывался → warning audit
тоже не эмитился. SOC не видел missing header.

После фикса: при soft-mode + missing header перед пропуском эмитим
`internal.dept_header_missing` (severity=WARNING). Симметрично с
`fetch_ipmi_credentials`, который всегда зовёт `_check_target_department`.
"""

from __future__ import annotations

import pytest

from src.services import audit_service, internal_service
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

    monkeypatch.setattr(internal_service.account_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(internal_service.account_repo, "update_password", update_password)


def _stub_permissions_ok(monkeypatch):
    async def require_action(db, identity, et, ac):
        return None

    monkeypatch.setattr(internal_service.permissions, "require_action", require_action)


def _stub_secrets(monkeypatch):
    monkeypatch.setattr(
        internal_service.secrets_service,
        "decrypt",
        lambda c, **_kw: "plaintext",
    )
    monkeypatch.setattr(
        internal_service.secrets_service,
        "encrypt",
        lambda p, **_kw: "v1$nonce$cipher",
    )
    # `aad_for_*` helpers могут отсутствовать (если secrets_service ещё не
    # дотянулся), но если есть — стабим в no-op строку.
    for attr in ("aad_for_server_account_password", "aad_for_ipmi_credential"):
        if hasattr(internal_service.secrets_service, attr):
            monkeypatch.setattr(
                internal_service.secrets_service, attr,
                lambda _id, _attr=attr: f"{_attr}:{_id}",
            )


@pytest.fixture
def captured_emits(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, **kwargs):
        captured.append({"action": action, **kwargs})

    monkeypatch.setattr(audit_service, "emit", fake_emit)
    # Internal_service импортирует audit_service модулем, патч идёт автоматом.
    return captured


def _identity():
    from src.schemas.identity import IdentityContext

    return IdentityContext(
        user_id="bot_w",
        username="server_worker_user",
        department_id="dep_a",
        service_roles={"server_service": ["worker_bot"]},
        subject_type="bot",
    )


def _settings(monkeypatch, *, strict: bool):
    class _S:
        internal_require_dept_header = strict

    monkeypatch.setattr(internal_service, "get_settings", lambda: _S())


def _stub_server_repo(monkeypatch, *, server_id: str, department_id: str | None = "dep_a"):
    """Заглушка для server repository — `_check_target_department` теперь
    зовётся безусловно и требует `server.department_id` для actor-vs-server
    cross-check'а."""
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
async def test_fetch_account_password_soft_mode_no_header_emits_warning(
    monkeypatch, captured_emits, db,
):
    """Soft mode + missing header → `internal.dept_header_missing` WARNING."""
    _settings(monkeypatch, strict=False)
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    await internal_service.fetch_account_password(
        db, _identity(), server_id="srv_1", account_id="acc_1", target_department_id=None,
    )

    warns = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert len(warns) == 1
    w = warns[0]
    assert w["status"] == "warning"
    assert w["allowed"] is True
    assert w["details"]["soft_mode"] is True
    assert w["details"]["server_id"] == "srv_1"
    assert w["details"]["path"] == "internal.fetch_account_password"
    assert w["actor_id"] == "bot_w"


@pytest.mark.asyncio
async def test_rotate_account_password_soft_mode_no_header_emits_warning(
    monkeypatch, captured_emits, db,
):
    """Соответствующее поведение для rotate-callback'а."""
    _settings(monkeypatch, strict=False)
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    await internal_service.rotate_account_password(
        db, _identity(), server_id="srv_1", account_id="acc_1",
        new_password="newpwd", target_department_id=None,
    )

    warns = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert len(warns) == 1
    assert warns[0]["details"]["path"] == "internal.rotate_account_password"


@pytest.mark.asyncio
async def test_strict_mode_no_header_does_not_emit_dept_header_missing(
    monkeypatch, captured_emits, db,
):
    """Strict mode идёт по `_check_target_department` ветке — она эмитит
    свой `denied`-audit; `internal.dept_header_missing` НЕ эмитится.
    """
    _settings(monkeypatch, strict=True)
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")

    # Стаб server_repo — нужен `_check_target_department`'у.
    class _Server:
        department_id = "dep_a"

    async def get_by_id(db, sid):
        return _Server()

    monkeypatch.setattr(internal_service.server_repo, "get_by_id", get_by_id)

    from src.core.exceptions import AuthorizationError

    with pytest.raises(AuthorizationError):
        await internal_service.fetch_account_password(
            db, _identity(), server_id="srv_1", account_id="acc_1", target_department_id=None,
        )

    # Strict-режим эмитит `server_account.view_password` denied, но НЕ
    # `internal.dept_header_missing` (та ветка идёт только в soft).
    missing = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert missing == []


def test_audit_events_catalog_contains_dept_header_missing():
    """`internal.dept_header_missing` зарегистрирован в SERVICE_EVENTS."""
    actions = {e["action"] for e in SERVICE_EVENTS}
    assert "internal.dept_header_missing" in actions
    entry = next(e for e in SERVICE_EVENTS if e["action"] == "internal.dept_header_missing")
    assert entry["default_severity"] == "WARNING"


@pytest.mark.asyncio
async def test_fetch_account_password_actor_mismatch_soft_emits_denied(
    monkeypatch, captured_emits, db,
):
    """Soft mode + actor.dept != server.dept → 403 + denied audit с
    `reason=actor_department_mismatch`. Header-missing warning **не** должен
    эмититься: actor-проверка падает раньше.
    """
    _settings(monkeypatch, strict=False)
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")

    class _Server:
        department_id = "dep_a"

    async def get_by_id(db, sid):
        return _Server()

    monkeypatch.setattr(internal_service.server_repo, "get_by_id", get_by_id)

    from src.core.exceptions import AuthorizationError
    from src.schemas.identity import IdentityContext

    foreign_identity = IdentityContext(
        user_id="bot_w",
        username="server_worker_user",
        department_id="dep_b",  # отличается от сервера
        service_roles={"server_service": ["worker_bot"]},
        subject_type="bot",
    )

    with pytest.raises(AuthorizationError) as ei:
        await internal_service.fetch_account_password(
            db, foreign_identity, server_id="srv_1", account_id="acc_1",
            target_department_id=None,
        )
    assert ei.value.error_code == "TARGET_DEPARTMENT_MISMATCH"

    denied = [
        e for e in captured_emits
        if e["action"] == "server_account.view_password" and e.get("status") == "denied"
    ]
    assert len(denied) == 1
    assert denied[0]["details"]["reason"] == "actor_department_mismatch"
    assert denied[0]["details"]["actor_department_id"] == "dep_b"
    assert denied[0]["details"]["server_department_id"] == "dep_a"
    # Header-missing warning не должен дойти — actor-check падает раньше.
    missing = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert missing == []


@pytest.mark.asyncio
async def test_rotate_account_password_actor_mismatch_soft_emits_denied(
    monkeypatch, captured_emits, db,
):
    """Симметрично для rotate-callback'а — actor-mismatch блокирует в soft."""
    _settings(monkeypatch, strict=False)
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_account_repo(monkeypatch, server_id="srv_1", account_id="acc_1")

    class _Server:
        department_id = "dep_a"

    async def get_by_id(db, sid):
        return _Server()

    monkeypatch.setattr(internal_service.server_repo, "get_by_id", get_by_id)

    from src.core.exceptions import AuthorizationError
    from src.schemas.identity import IdentityContext

    foreign_identity = IdentityContext(
        user_id="bot_w",
        username="server_worker_user",
        department_id="dep_b",
        service_roles={"server_service": ["worker_bot"]},
        subject_type="bot",
    )

    with pytest.raises(AuthorizationError) as ei:
        await internal_service.rotate_account_password(
            db, foreign_identity, server_id="srv_1", account_id="acc_1",
            new_password="injected", target_department_id=None,
        )
    assert ei.value.error_code == "TARGET_DEPARTMENT_MISMATCH"

    denied = [
        e for e in captured_emits
        if e["action"] == "server_account.rotate_password" and e.get("status") == "denied"
    ]
    assert len(denied) == 1
    assert denied[0]["details"]["reason"] == "actor_department_mismatch"


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
