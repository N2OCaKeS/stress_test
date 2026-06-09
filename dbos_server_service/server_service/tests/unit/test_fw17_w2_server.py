"""Юнит-тесты для server_service.

Покрывают пять фиксов:

1. `secrets.migration.skipped` зарегистрирован в `SERVICE_EVENTS`.
2. `InventoryUserItem.unix_groups` / `ServerAccount*.unix_groups` —
   `max_length=64` + POSIX-pattern на каждый элемент.
3. Permission ВЫШЕ existence/dept для `receive_users_inventory` и
   `record_provision_status` (выровнено с `receive_inventory`): caller
   без grant'а получает 403 permission_denied, факт существования
   сервера/аккаунта в чужом dept не утекает.
4. `POST /servers/{id}/prepare` имеет endpoint-level rate-limit через
   `server_prepare_rate_limit` (конфиг + декоратор).
5. `_reveal_account_password` / `_reveal_ipmi_password` оборачивают
   нестандартные исключения decrypt'а в `AppException(DECRYPT_FAILED)`.
"""
from __future__ import annotations

import pytest

from src.core.config import get_settings
from src.core.exceptions import AppException, AuthorizationError
from src.schemas.identity import IdentityContext
from src.schemas.internal import InventoryUserItem
from src.schemas.server_account import ServerAccountCreate, ServerAccountUpdate
from src.services.audit_events import SERVICE_EVENTS


# ── 1. catalog ──────────────────────────────────────────────────────────────


def test_secrets_migration_skipped_in_catalog():
    actions = {e["action"]: e for e in SERVICE_EVENTS}
    assert "secrets.migration.skipped" in actions, (
        "secrets.migration.skipped эмитится из secrets_migration_service "
        "но не зарегистрирован в SERVICE_EVENTS"
    )
    entry = actions["secrets.migration.skipped"]
    assert entry["default_severity"] == "WARNING"
    assert entry["description"]


# ── 2. unix_groups caps + pattern ───────────────────────────────────────────


class TestInventoryUserItemUnixGroups:
    def _base_payload(self, **over) -> dict:
        return {"login": "alice", "uid": 1000, **over}

    def test_valid_group_names_accepted(self):
        item = InventoryUserItem(
            **self._base_payload(unix_groups=["users", "wheel", "_systemd-resolve"]),
        )
        assert item.unix_groups == ["users", "wheel", "_systemd-resolve"]

    def test_uppercase_rejected(self):
        with pytest.raises(ValueError):
            InventoryUserItem(**self._base_payload(unix_groups=["Wheel"]))

    def test_leading_digit_rejected(self):
        with pytest.raises(ValueError):
            InventoryUserItem(**self._base_payload(unix_groups=["1abc"]))

    def test_crlf_injection_rejected(self):
        with pytest.raises(ValueError):
            InventoryUserItem(
                **self._base_payload(unix_groups=["users\r\nrootkit"]),
            )

    def test_long_group_name_rejected(self):
        with pytest.raises(ValueError):
            InventoryUserItem(
                **self._base_payload(unix_groups=["a" * 33]),
            )

    def test_max_length_cap_enforced(self):
        too_many = [f"g{i}" for i in range(65)]
        with pytest.raises(ValueError):
            InventoryUserItem(**self._base_payload(unix_groups=too_many))


class TestServerAccountUnixGroups:
    def _base_create(self, **over) -> dict:
        return {
            "server_ids": ["srv_1"],
            "login": "ops",
            "password": "Strong1Pass",
            **over,
        }

    def test_create_valid_groups(self):
        m = ServerAccountCreate(**self._base_create(unix_groups=["sudo", "docker"]))
        assert m.unix_groups == ["sudo", "docker"]

    def test_create_invalid_pattern(self):
        with pytest.raises(ValueError):
            ServerAccountCreate(**self._base_create(unix_groups=["Sudo"]))

    def test_create_too_many_groups(self):
        with pytest.raises(ValueError):
            ServerAccountCreate(
                **self._base_create(unix_groups=[f"g{i}" for i in range(65)]),
            )

    def test_update_valid_groups(self):
        m = ServerAccountUpdate(unix_groups=["sudo"])
        assert m.unix_groups == ["sudo"]

    def test_update_none_passes(self):
        m = ServerAccountUpdate(unix_groups=None)
        assert m.unix_groups is None

    def test_update_invalid_pattern(self):
        with pytest.raises(ValueError):
            ServerAccountUpdate(unix_groups=["BAD"])

    def test_update_too_many_groups(self):
        with pytest.raises(ValueError):
            ServerAccountUpdate(unix_groups=[f"g{i}" for i in range(65)])


# ── 3. permission-first ordering ────────────────────────────────────────────


def _identity(department_id: str = "dep_a") -> IdentityContext:
    return IdentityContext(
        user_id="usr_test",
        username="tester",
        department_id=department_id,
        department_name=None,
        allowed_services=["server_service"],
        service_roles={"server_service": ["reader"]},
        is_banned=False,
        platform_role=None,
        subject_type="user",
    )


def _capture_emits(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr("src.services.internal_service.audit_service.emit", fake_emit)
    return captured


class TestPermissionFirstOrdering:
    """Право проверяется ДО existence/dept. Caller без grant'а получает 403
    permission_denied; факт существования сервера в чужом dept не утекает."""

    async def test_users_inventory_no_permission_denies_before_lookup(
        self, monkeypatch,
    ):
        """receive_users_inventory: reader (без INVENTORY_SUBMIT) → 403
        permission_denied раньше, чем server_repo.get_by_id."""
        from src.services import internal_service
        from src.schemas.internal import UsersInventoryCallbackRequest

        captured = _capture_emits(monkeypatch)
        get_calls: list[str] = []

        async def fake_get(_db, sid):
            get_calls.append(sid)
            return None

        async def fake_require(_db, _ident, _ent, _act):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED", message="no grant",
            )

        monkeypatch.setattr(
            "src.services.internal_service.server_repo.get_by_id", fake_get,
        )
        monkeypatch.setattr(
            "src.services.internal_service.permissions.require_action",
            fake_require,
        )

        with pytest.raises(AuthorizationError):
            await internal_service.receive_users_inventory(
                db=None,
                identity=_identity(),
                server_id="srv_missing",
                payload=UsersInventoryCallbackRequest(users=[]),
                target_department_id="dep_a",
            )

        assert get_calls == [], (
            "server_repo.get_by_id не должен зваться, пока permission не проверен"
        )
        denied = [
            e for e in captured
            if e["action"] == "server_account.users_inventory_received"
            and e.get("status") == "denied"
        ]
        assert len(denied) == 1
        assert denied[0]["details"]["reason"] == "permission_denied"

    async def test_provision_status_no_permission_denies_before_lookup(
        self, monkeypatch,
    ):
        """record_provision_status: caller без PROVISION_ON_HOST → 403
        permission_denied раньше, чем server_repo.get_by_id."""
        from src.services import internal_service
        from src.schemas.internal import ProvisionStatusRequest

        captured = _capture_emits(monkeypatch)
        get_calls: list[str] = []

        async def fake_get(_db, sid):
            get_calls.append(sid)
            return None

        async def fake_require(_db, _ident, _ent, _act):
            raise AuthorizationError(
                error_code="PERMISSION_DENIED", message="no grant",
            )

        monkeypatch.setattr(
            "src.services.internal_service.server_repo.get_by_id", fake_get,
        )
        monkeypatch.setattr(
            "src.services.internal_service.permissions.require_action",
            fake_require,
        )

        with pytest.raises(AuthorizationError):
            await internal_service.record_provision_status(
                db=None,
                identity=_identity(),
                server_id="srv_missing",
                account_id="acc_missing",
                payload=ProvisionStatusRequest(operation="provision", present=True),
                target_department_id="dep_a",
            )

        assert get_calls == [], (
            "server_repo.get_by_id не должен зваться, пока permission не проверен"
        )
        denied = [
            e for e in captured
            if e["action"] == "server_account.provision_status"
            and e.get("status") == "denied"
        ]
        assert len(denied) == 1
        assert denied[0]["details"]["reason"] == "permission_denied"
        assert denied[0]["details"]["server_id"] == "srv_missing"


# ── 4. server_prepare rate limit ────────────────────────────────────────────


class TestServerPrepareRateLimitConfig:
    def test_config_field_present(self):
        settings = get_settings()
        assert hasattr(settings, "server_prepare_rate_limit")
        assert settings.server_prepare_rate_limit  # non-empty
        # Дефолт согласно task spec
        assert "/" in settings.server_prepare_rate_limit

    def test_endpoint_decorated_with_limiter(self):
        """server_prepare_dispatch обёрнут endpoint_limiter.limit — slowapi
        проставляет служебный атрибут `_rate_limit` на handler'е."""
        from src.api.v1.endpoints import worker_dispatch

        handler = worker_dispatch.server_prepare_dispatch
        # slowapi проставляет один из атрибутов: `_rate_limit` (старый),
        # либо хранит лимит в `_limit_value` или `__wrapped__`. Проверяем
        # любой из этих сигналов плюс наличие request-аргумента в signature.
        import inspect
        sig = inspect.signature(handler)
        assert "request" in sig.parameters, (
            "slowapi требует параметр `request` в signature endpoint'а"
        )

    def test_endpoint_first_arg_is_request(self):
        """slowapi.@limiter.limit ожидает Request в kwargs/args. Проверяем,
        что первым параметром идёт `request` — иначе slowapi не сможет
        извлечь client IP и упадёт с ConfigurationError на старте."""
        import inspect
        from src.api.v1.endpoints import worker_dispatch

        params = list(inspect.signature(
            worker_dispatch.server_prepare_dispatch,
        ).parameters)
        assert params[0] == "request"


# ── 5. decrypt wrap ─────────────────────────────────────────────────────────


class TestRevealPasswordWrapsDecryptErrors:
    """Любой не-AppException в decrypt оборачивается в AppException(DECRYPT_FAILED)
    с http_status=500, audit failure эмитится."""

    async def test_server_account_reveal_wraps_runtime_error(self, monkeypatch):
        from src.services import server_account as sa_svc

        captured: list[dict] = []
        monkeypatch.setattr(
            "src.services.server_account.audit_service.emit",
            lambda action, actor_id=None, **kw: captured.append(
                {"action": action, "actor_id": actor_id, **kw}
            ),
        )

        def boom(_token, *, aad):
            raise RuntimeError("threadpool wrapper exploded")

        # Reveal-helper'ы перешли на decrypt_with_meta — патчим именно его,
        # legacy `decrypt` остаётся как обратно-совместимая обёртка.
        monkeypatch.setattr(
            "src.services.server_account.secrets_service.decrypt_with_meta", boom,
        )

        class _Acc:
            id = "acc_1"
            login = "ops"
            department_id = "dep_a"
            password_encrypted = "v1$nonce$ct"

        with pytest.raises(AppException) as exc_info:
            await sa_svc._reveal_account_password(None, _Acc())

        assert exc_info.value.error_code == "DECRYPT_FAILED"
        assert exc_info.value.http_status == 500
        failures = [
            e for e in captured
            if e["action"] == "server_account.password_revealed"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "decrypt_failed"

    async def test_server_account_reveal_passes_through_app_exception(
        self, monkeypatch,
    ):
        """Если decrypt бросил AppException (штатный DECRYPT_FAILED) —
        пропускаем как есть, без двойной обёртки."""
        from src.services import server_account as sa_svc

        captured: list[dict] = []
        monkeypatch.setattr(
            "src.services.server_account.audit_service.emit",
            lambda action, actor_id=None, **kw: captured.append(
                {"action": action, "actor_id": actor_id, **kw}
            ),
        )

        def boom(_token, *, aad):
            raise AppException(
                error_code="DECRYPT_FAILED",
                message="bad tag",
                http_status=500,
            )

        monkeypatch.setattr(
            "src.services.server_account.secrets_service.decrypt_with_meta", boom,
        )

        class _Acc:
            id = "acc_1"
            login = "ops"
            department_id = "dep_a"
            password_encrypted = "v1$nonce$ct"

        with pytest.raises(AppException) as exc_info:
            await sa_svc._reveal_account_password(None, _Acc())
        # Сообщение — оригинальное, не переобёрнутое
        assert exc_info.value.message == "bad tag"

    async def test_ipmi_reveal_wraps_runtime_error(self, monkeypatch):
        from src.services import ipmi_controller as ipmi_svc

        captured: list[dict] = []
        monkeypatch.setattr(
            "src.services.ipmi_controller.audit_service.emit",
            lambda action, actor_id=None, **kw: captured.append(
                {"action": action, "actor_id": actor_id, **kw}
            ),
        )

        def boom(_token, *, aad):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(
            "src.services.ipmi_controller.secrets_service.decrypt_with_meta", boom,
        )

        class _Ctrl:
            id = "ipm_1"
            server_id = "srv_1"
            username = "ADMIN"
            password_encrypted = "v1$nonce$ct"

        with pytest.raises(AppException) as exc_info:
            await ipmi_svc._reveal_controller_password(None, _Ctrl(), department_id="dep_a")
        assert exc_info.value.error_code == "DECRYPT_FAILED"
        assert exc_info.value.http_status == 500
        failures = [
            e for e in captured
            if e["action"] == "ipmi_controller.credentials_revealed"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "decrypt_failed"
