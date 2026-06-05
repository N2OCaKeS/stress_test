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
    # Internal_service импортирует audit_service модулем, default patch покрывает обоих.
    return make_emit_capture(monkeypatch)


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
        # IPMI verify max age — реальное значение по умолчанию (60s).
        # record_ipmi_credentials_rotated читает его при проверке verified_at.
        ipmi_verify_max_age_seconds = 60
        # Окно допустимого NTP-skew для rotated_at (default из core/config.py).
        rotated_at_skew_seconds = 600
        # Future-skew окно для verified_at — отдельное от max_age, чтобы
        # на стендах с NTP-drift'ом не отбивать BMC verify.
        verify_future_skew_seconds = 60

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
    """Soft mode + actor.dept != server.dept → 404 ACCOUNT_NOT_FOUND
    (унификация cross-dept под существование) + denied audit с
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

    from src.core.exceptions import NotFoundError
    from src.schemas.identity import IdentityContext

    foreign_identity = IdentityContext(
        user_id="bot_w",
        username="server_worker_user",
        department_id="dep_b",  # отличается от сервера
        service_roles={"server_service": ["worker_bot"]},
        subject_type="bot",
    )

    with pytest.raises(NotFoundError) as ei:
        await internal_service.fetch_account_password(
            db, foreign_identity, server_id="srv_1", account_id="acc_1",
            target_department_id=None,
        )
    assert ei.value.error_code == "ACCOUNT_NOT_FOUND"

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
    """Симметрично для rotate-callback'а — actor-mismatch блокирует в soft.
    Cross-dept унифицирован под ACCOUNT_NOT_FOUND, чтобы не выдавать
    enumeration-oracle по разнице 403/404.
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

    from src.core.exceptions import NotFoundError
    from src.schemas.identity import IdentityContext

    foreign_identity = IdentityContext(
        user_id="bot_w",
        username="server_worker_user",
        department_id="dep_b",
        service_roles={"server_service": ["worker_bot"]},
        subject_type="bot",
    )

    with pytest.raises(NotFoundError) as ei:
        await internal_service.rotate_account_password(
            db, foreign_identity, server_id="srv_1", account_id="acc_1",
            new_password="injected", target_department_id=None,
        )
    assert ei.value.error_code == "ACCOUNT_NOT_FOUND"

    denied = [
        e for e in captured_emits
        if e["action"] == "server_account.rotate_password" and e.get("status") == "denied"
    ]
    assert len(denied) == 1
    assert denied[0]["details"]["reason"] == "actor_department_mismatch"


@pytest.mark.asyncio
async def test_fetch_ipmi_credentials_soft_mode_no_header_emits_warning(
    monkeypatch, captured_emits, db,
):
    """fetch_ipmi_credentials: soft + missing header → `internal.dept_header_missing`."""
    _settings(monkeypatch, strict=False)
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

    await internal_service.fetch_ipmi_credentials(
        db, _identity(), server_id="srv_1", target_department_id=None,
    )

    warns = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert len(warns) == 1
    assert warns[0]["details"]["path"] == "internal.fetch_ipmi_credentials"
    assert warns[0]["details"]["caller_type"] == "bot"


@pytest.mark.asyncio
async def test_receive_inventory_soft_mode_no_header_emits_warning(
    monkeypatch, captured_emits, db,
):
    """receive_inventory: soft + missing header → warning."""
    _settings(monkeypatch, strict=False)
    _stub_permissions_ok(monkeypatch)
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    async def _noop(*a, **kw):
        return None

    async def _resolve_os(*a, **kw):
        return "osv_1"

    async def _upsert(*a, **kw):
        return 0

    monkeypatch.setattr(internal_service.server_repo, "update", _noop)
    monkeypatch.setattr(internal_service, "_resolve_or_create_os", _resolve_os)
    monkeypatch.setattr(internal_service, "_upsert_disks", _upsert)

    class _Sess:
        async def commit(self):
            return None

    from src.schemas.internal import InventoryCallbackRequest

    payload = InventoryCallbackRequest(
        hostname="h", kernel="k", cpu_brand=None, cpu_model=None,
        cpu_cores=1, cpu_threads=None, cpu_frequency_ghz=None,
        os_version="Astra", disks=[],
    )
    await internal_service.receive_inventory(
        _Sess(), _identity(), server_id="srv_1", payload=payload,
        target_department_id=None,
    )

    warns = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert len(warns) == 1
    assert warns[0]["details"]["path"] == "internal.receive_inventory"


@pytest.mark.asyncio
async def test_record_provision_status_soft_mode_no_header_emits_warning(
    monkeypatch, captured_emits, db,
):
    """record_provision_status: soft + missing header → warning."""
    _settings(monkeypatch, strict=False)
    _stub_permissions_ok(monkeypatch)
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    class _Account:
        id = "acc_1"
        login = "root"
        # record_provision_status читает флаг, чтобы снять pending_apply на
        # успешном callback'е. Стартовое значение False — путь «уже применено,
        # ничего не трогаем», для проверки soft-warning'а этого достаточно.
        credentials_pending_apply = False

    class _Link:
        pass

    async def get_by_id(db, aid):
        return _Account()

    async def get_for_update(db, aid):
        return _Account()

    async def get_link(db, aid, sid):
        return _Link()

    async def set_link_presence(db, link, present):
        return None

    monkeypatch.setattr(internal_service.account_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(internal_service.account_repo, "get_for_update", get_for_update)
    monkeypatch.setattr(internal_service.account_repo, "get_link", get_link)
    monkeypatch.setattr(internal_service.account_repo, "set_link_presence", set_link_presence)

    class _Sess:
        async def commit(self):
            return None

    from src.schemas.internal import ProvisionStatusRequest

    payload = ProvisionStatusRequest(operation="provision", present=True)
    await internal_service.record_provision_status(
        _Sess(), _identity(), server_id="srv_1", account_id="acc_1",
        payload=payload, target_department_id=None,
    )

    warns = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert len(warns) == 1
    assert warns[0]["details"]["path"] == "internal.record_provision_status"


@pytest.mark.asyncio
async def test_record_server_prepared_soft_mode_no_header_emits_warning(
    monkeypatch, captured_emits, db,
):
    """record_server_prepared: soft + missing header → warning."""
    _settings(monkeypatch, strict=False)
    _stub_permissions_ok(monkeypatch)
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(internal_service.server_repo, "update", _noop)

    class _Sess:
        async def commit(self):
            return None

    from src.schemas.server import ServerPrepareCallbackRequest

    payload = ServerPrepareCallbackRequest(management_user="dbos_mgmt")
    await internal_service.record_server_prepared(
        _Sess(), _identity(), server_id="srv_1", payload=payload,
        target_department_id=None,
    )

    warns = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert len(warns) == 1
    assert warns[0]["details"]["path"] == "internal.record_server_prepared"


@pytest.mark.asyncio
async def test_record_ipmi_credentials_rotated_soft_mode_no_header_emits_warning(
    monkeypatch, captured_emits, db,
):
    """record_ipmi_credentials_rotated: soft + missing header → warning."""
    from datetime import datetime, timezone

    _settings(monkeypatch, strict=False)
    _stub_permissions_ok(monkeypatch)
    _stub_secrets(monkeypatch)
    _stub_server_repo(monkeypatch, server_id="srv_1", department_id="dep_a")

    class _Ctrl:
        id = "ctrl_1"
        server_id = "srv_1"
        password_encrypted = None
        password_rotated_at = None

    async def get_by_id(db, cid):
        return _Ctrl()

    async def update(db, ctrl, fields):
        return None

    monkeypatch.setattr(internal_service.ipmi_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(internal_service.ipmi_repo, "update", update)

    class _Sess:
        async def commit(self):
            return None

    from src.schemas.internal import IpmiCredentialsRotatedRequest

    payload = IpmiCredentialsRotatedRequest(
        new_password="Strong1Password",
        rotated_at=datetime.now(timezone.utc),
        verified_at=datetime.now(timezone.utc),
    )
    await internal_service.record_ipmi_credentials_rotated(
        _Sess(), _identity(), controller_id="ctrl_1", payload=payload,
        target_department_id=None,
    )

    warns = [e for e in captured_emits if e["action"] == "internal.dept_header_missing"]
    assert len(warns) == 1
    assert warns[0]["details"]["path"] == "internal.record_ipmi_credentials_rotated"


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
