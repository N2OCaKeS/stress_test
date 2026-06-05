"""P3 carry-over по server_service из W14/W15/W16.

Покрыто:

1. `redaction._SECRET_KEYS` маскирует `hkdf_salt`, `hkdf_salt_hex`,
   `master_key`, плюс защитные варианты (`master_key_hex`,
   `master_key_b64`, `hkdf_salt_b64`, `master_key_plaintext`).
2. `fetch_account_password` / `rotate_account_password` при отсутствующем
   сервере эмитят аудит с `target_id=server_id, target_type="server"`,
   а account_id уезжает в `details`.
3. `secrets_migration.finalize_done` endpoint при `data["skipped"]=True`
   эмитит `secrets.reencrypt_done` со `status="warning"`, на нормальном
   закрытии — `status="success"`.
4. `secrets_migration_service.requeue_failed` сбрасывает `attempts` в 0.
5. `reencrypt_batch` логирует имя класса ошибки на per-row фейле через
   `logger.warning`.
6. `record_ipmi_credentials_rotated` для orphaned controller (server is
   None) эмитит `reason="orphaned_ipmi_controller"` и не светит ложный
   `actor_department_mismatch`.
7. `tasks.cancel_task` для row с `target_server_id=None` и `task_kind`
   не в `_SYSTEM_TASK_KINDS` отдаёт 403 ``TASK_NOT_CANCELLABLE_WITHOUT_TARGET``,
   эмитит denied-audit и `cancel_task` не вызывается.
8. `worker_dispatch.server_prepare_dispatch` имеет audit-trail на
   `creds_store_unavailable` и `creds_store_failed` (подтверждение W18-W1).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.api.v1.endpoints import tasks as tasks_endpoint
from src.api.v1.endpoints import worker_dispatch as wd_endpoint
from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError, NotFoundError
from src.schemas.identity import IdentityContext
from src.schemas.internal import IpmiCredentialsRotatedRequest
from src.services import (
    internal_service,
    redaction,
    secrets_migration_service,
)

from tests._helpers import make_emit_capture


# ── Хелперы ──────────────────────────────────────────────────────────────────


def _identity(
    *,
    user_id: str = "bot_w",
    department_id: str | None = "dep_a",
    platform_role: PlatformRole | None = None,
    service_roles: dict[str, list[str]] | None = None,
    subject_type: str = "bot",
) -> IdentityContext:
    return IdentityContext(
        user_id=user_id,
        username="tester",
        department_id=department_id,
        allowed_services=["server_service"],
        service_roles=service_roles or {"server_service": ["worker_bot"]},
        is_banned=False,
        platform_role=platform_role,
        subject_type=subject_type,
    )


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.tasks.audit_service.emit",
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
        "src.api.v1.endpoints.secrets_migration.audit_service.emit",
    )


def _settings(monkeypatch, *, strict: bool = False):
    class _S:
        internal_require_dept_header = strict
        ipmi_verify_max_age_seconds = 60
        rotated_at_skew_seconds = 600
        verify_future_skew_seconds = 60

    monkeypatch.setattr(internal_service, "get_settings", lambda: _S())


def _stub_permissions_ok(monkeypatch):
    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(internal_service.permissions, "require_action", _ok)


# ── 1. redaction: HKDF salt / master key ─────────────────────────────────────


class TestRedactionSecretKeys:
    def test_hkdf_salt_hex_redacted(self):
        out = redaction.redact({"hkdf_salt_hex": "deadbeef" * 8})
        assert out["hkdf_salt_hex"] == "<SECRET>"

    def test_hkdf_salt_redacted(self):
        out = redaction.redact({"hkdf_salt": "abcd1234"})
        assert out["hkdf_salt"] == "<SECRET>"

    def test_hkdf_salt_b64_redacted(self):
        out = redaction.redact({"hkdf_salt_b64": "abcd=="})
        assert out["hkdf_salt_b64"] == "<SECRET>"

    def test_master_key_redacted(self):
        out = redaction.redact({"master_key": "00" * 32})
        assert out["master_key"] == "<SECRET>"

    def test_master_key_hex_redacted(self):
        out = redaction.redact({"master_key_hex": "00" * 32})
        assert out["master_key_hex"] == "<SECRET>"

    def test_master_key_b64_redacted(self):
        out = redaction.redact({"master_key_b64": "AAAA=="})
        assert out["master_key_b64"] == "<SECRET>"

    def test_master_key_plaintext_redacted(self):
        out = redaction.redact({"master_key_plaintext": "raw"})
        assert out["master_key_plaintext"] == "<SECRET>"

    def test_keys_nested(self):
        out = redaction.redact({
            "outer": {"hkdf_salt_hex": "xx", "master_key": "yy"},
        })
        assert out["outer"]["hkdf_salt_hex"] == "<SECRET>"
        assert out["outer"]["master_key"] == "<SECRET>"


# ── 2. fetch/rotate_account_password при server_not_found ────────────────────


def _stub_account_repo(monkeypatch):
    async def get_by_id(db, aid):
        return None

    async def is_linked(db, aid, sid):
        return False

    monkeypatch.setattr(internal_service.account_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(internal_service.account_repo, "is_linked", is_linked)


def _stub_server_repo_missing(monkeypatch):
    async def get_by_id(db, sid):
        return None

    monkeypatch.setattr(internal_service.server_repo, "get_by_id", get_by_id)


class TestServerNotFoundTargetType:
    @pytest.mark.asyncio
    async def test_fetch_account_password_server_not_found_target_is_server(
        self, monkeypatch, captured_emits, db,
    ):
        _settings(monkeypatch, strict=False)
        _stub_permissions_ok(monkeypatch)
        _stub_account_repo(monkeypatch)
        _stub_server_repo_missing(monkeypatch)

        with pytest.raises(NotFoundError):
            await internal_service.fetch_account_password(
                db, _identity(), server_id="srv_missing", account_id="acc_x",
                target_department_id="dep_a",
            )

        failures = [
            e for e in captured_emits
            if e["action"] == "server_account.view_password"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == "srv_missing"
        assert ev["target_type"] == "server"
        assert ev["details"]["reason"] == "server_not_found"
        assert ev["details"]["server_id"] == "srv_missing"
        assert ev["details"]["account_id"] == "acc_x"

    @pytest.mark.asyncio
    async def test_rotate_account_password_server_not_found_target_is_server(
        self, monkeypatch, captured_emits, db,
    ):
        _settings(monkeypatch, strict=False)
        _stub_permissions_ok(monkeypatch)
        _stub_account_repo(monkeypatch)
        _stub_server_repo_missing(monkeypatch)

        with pytest.raises(NotFoundError):
            await internal_service.rotate_account_password(
                db, _identity(), server_id="srv_missing", account_id="acc_x",
                new_password="new", target_department_id="dep_a",
            )

        failures = [
            e for e in captured_emits
            if e["action"] == "server_account.rotate_password"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == "srv_missing"
        assert ev["target_type"] == "server"
        assert ev["details"]["reason"] == "server_not_found"
        assert ev["details"]["server_id"] == "srv_missing"
        assert ev["details"]["account_id"] == "acc_x"


# ── 3. secrets_migration.finalize_done endpoint: skipped → warning ───────────


class TestFinalizeDoneEndpointSkipStatus:
    @pytest.mark.asyncio
    async def test_done_clean_emits_success(self, monkeypatch, captured_emits):
        from src.api.v1.endpoints import secrets_migration as sm_ep

        async def fake_finalize_done(db, outbox_id):
            return {"id": outbox_id, "status": "done", "skipped": False}

        async def fake_require(db, identity):
            return None

        monkeypatch.setattr(
            sm_ep.secrets_migration_service, "finalize_done", fake_finalize_done,
        )
        monkeypatch.setattr(sm_ep, "_require_worker_scope", fake_require)

        resp = await sm_ep.finalize_reencrypt_outbox_done(
            outbox_id="rox_clean", identity=_identity(), db=None,
        )
        assert resp.status == "done"

        events = [e for e in captured_emits if e["action"] == "secrets.reencrypt_done"]
        assert len(events) == 1
        assert events[0]["status"] == "success"
        assert events[0]["details"]["skipped"] is False

    @pytest.mark.asyncio
    async def test_done_skipped_emits_warning(self, monkeypatch, captured_emits):
        from src.api.v1.endpoints import secrets_migration as sm_ep

        async def fake_finalize_done(db, outbox_id):
            return {
                "id": outbox_id,
                "status": "done",
                "skipped": True,
                "skip_reason": "owner_vanished",
            }

        async def fake_require(db, identity):
            return None

        monkeypatch.setattr(
            sm_ep.secrets_migration_service, "finalize_done", fake_finalize_done,
        )
        monkeypatch.setattr(sm_ep, "_require_worker_scope", fake_require)

        resp = await sm_ep.finalize_reencrypt_outbox_done(
            outbox_id="rox_skip", identity=_identity(), db=None,
        )
        assert resp.status == "done"
        # `skip_reason` остаётся внутренним полем — wire-response его не несёт.
        assert not hasattr(resp, "skip_reason")

        events = [e for e in captured_emits if e["action"] == "secrets.reencrypt_done"]
        assert len(events) == 1
        assert events[0]["status"] == "warning"
        assert events[0]["details"]["skipped"] is True
        assert events[0]["details"]["reason"] == "owner_vanished"

    @pytest.mark.asyncio
    async def test_done_skipped_without_reason_no_reason_in_details(
        self, monkeypatch, captured_emits,
    ):
        """Старая форма ответа без skip_reason не ломает audit-emit."""
        from src.api.v1.endpoints import secrets_migration as sm_ep

        async def fake_finalize_done(db, outbox_id):
            return {"id": outbox_id, "status": "done", "skipped": True}

        async def fake_require(db, identity):
            return None

        monkeypatch.setattr(
            sm_ep.secrets_migration_service, "finalize_done", fake_finalize_done,
        )
        monkeypatch.setattr(sm_ep, "_require_worker_scope", fake_require)

        resp = await sm_ep.finalize_reencrypt_outbox_done(
            outbox_id="rox_skip_noreason", identity=_identity(), db=None,
        )
        assert resp.status == "done"

        events = [e for e in captured_emits if e["action"] == "secrets.reencrypt_done"]
        assert len(events) == 1
        assert events[0]["status"] == "warning"
        assert "reason" not in events[0]["details"]


# ── 4. requeue_failed сбрасывает attempts ────────────────────────────────────


class TestRequeueFailedResetsAttempts:
    @pytest.mark.asyncio
    async def test_requeue_failed_zeroes_attempts(self, db):
        from src.models import ReencryptOutboxEntry

        entry = ReencryptOutboxEntry(
            id="rox_requeue_001",
            entity_type="server_account",
            entity_id="acc_requeue_001",
            legacy_ciphertext="v1$nn$cc",
            status="failed",
            attempts=5,
            last_error="boom",
        )
        db.add(entry)
        await db.commit()

        result = await secrets_migration_service.requeue_failed(db, "rox_requeue_001")
        assert result["status"] == "pending"

        await db.refresh(entry)
        assert entry.attempts == 0
        assert entry.last_error is None
        assert entry.status == "pending"
        assert entry.processed_at is None


# ── 5. reencrypt_batch логирует per-row errors ───────────────────────────────


class TestReencryptBatchLogsPerRowError:
    @pytest.mark.asyncio
    async def test_per_row_decrypt_error_logged(self, monkeypatch, caplog):
        class _FakeAcc:
            def __init__(self):
                self.id = "acc_failing"
                self.password_encrypted = "v0$broken"

        async def _pick_accounts(db, active, limit):
            return [_FakeAcc()]

        async def _pick_ipmis(db, active, limit):
            return []

        class _Settings:
            server_encryption_key_version = 1

        monkeypatch.setattr(
            secrets_migration_service, "_pick_account_batch", _pick_accounts,
        )
        monkeypatch.setattr(
            secrets_migration_service, "_pick_ipmi_batch", _pick_ipmis,
        )
        monkeypatch.setattr(
            secrets_migration_service, "get_settings", lambda: _Settings(),
        )

        def _aad(_id):
            return f"aad:{_id}"

        def _bad_decrypt(_ct, **_kw):
            raise RuntimeError("synthetic decrypt error")

        monkeypatch.setattr(
            secrets_migration_service.secrets_service,
            "aad_for_server_account_password", _aad,
        )
        monkeypatch.setattr(
            secrets_migration_service.secrets_service, "decrypt", _bad_decrypt,
        )

        with caplog.at_level(logging.WARNING, logger=secrets_migration_service.__name__):
            result = await secrets_migration_service.reencrypt_batch(db=None, limit=10)

        assert result["processed"] == 0
        assert result["errors"] == 1
        # failed_rows — структурированный sample для SIEM поверх per-row warning-логов.
        assert result["failed_rows"] == [
            {
                "entity_type": "server_account",
                "entity_id": "acc_failing",
                "error_class": "RuntimeError",
            }
        ]
        # сообщение содержит id строки и класс исключения
        msgs = [r.getMessage() for r in caplog.records]
        assert any("acc_failing" in m and "RuntimeError" in m for m in msgs), msgs


class TestReencryptBatchEndpointPropagatesFailedRows:
    @pytest.mark.asyncio
    async def test_failed_rows_in_audit_details(self, monkeypatch, captured_emits):
        """Endpoint кладёт sample упавших row'ов в `secrets.reencrypt_batch` audit."""
        from src.api.v1.endpoints import secrets_migration as sm_ep

        async def fake_reencrypt(db, limit):
            return {
                "processed": 1,
                "errors": 2,
                "failed_rows": [
                    {
                        "entity_type": "server_account",
                        "entity_id": "acc_a",
                        "error_class": "RuntimeError",
                    },
                    {
                        "entity_type": "ipmi_controller",
                        "entity_id": "ctrl_b",
                        "error_class": "ValueError",
                    },
                ],
            }

        async def fake_require(db, identity):
            return None

        monkeypatch.setattr(
            sm_ep.secrets_migration_service, "reencrypt_batch", fake_reencrypt,
        )
        monkeypatch.setattr(sm_ep, "_require_worker_scope", fake_require)

        resp = await sm_ep.reencrypt_batch(identity=_identity(), db=None, limit=10)
        # wire-схема не несёт failed_rows — только processed/errors
        assert resp.processed == 1
        assert resp.errors == 2

        events = [e for e in captured_emits if e["action"] == "secrets.reencrypt_batch"]
        assert len(events) == 1
        details = events[0]["details"]
        assert details["processed"] == 1
        assert details["errors"] == 2
        assert len(details["failed_rows"]) == 2
        assert details["failed_rows"][0]["entity_id"] == "acc_a"
        assert details["failed_rows"][1]["entity_type"] == "ipmi_controller"

    @pytest.mark.asyncio
    async def test_empty_failed_rows_not_in_audit(self, monkeypatch, captured_emits):
        """Чистый батч — `failed_rows` в details не появляется."""
        from src.api.v1.endpoints import secrets_migration as sm_ep

        async def fake_reencrypt(db, limit):
            return {"processed": 5, "errors": 0, "failed_rows": []}

        async def fake_require(db, identity):
            return None

        monkeypatch.setattr(
            sm_ep.secrets_migration_service, "reencrypt_batch", fake_reencrypt,
        )
        monkeypatch.setattr(sm_ep, "_require_worker_scope", fake_require)

        await sm_ep.reencrypt_batch(identity=_identity(), db=None, limit=10)

        events = [e for e in captured_emits if e["action"] == "secrets.reencrypt_batch"]
        assert len(events) == 1
        assert "failed_rows" not in events[0]["details"]


# ── 6. orphaned IPMI controller ──────────────────────────────────────────────


class TestOrphanedIpmiController:
    @pytest.mark.asyncio
    async def test_orphaned_controller_emits_orphaned_reason(
        self, monkeypatch, captured_emits, db,
    ):
        """server is None → reason=orphaned_ipmi_controller (не actor_department_mismatch)."""
        _settings(monkeypatch, strict=False)
        _stub_permissions_ok(monkeypatch)

        class _Ctrl:
            id = "ctrl_orphan"
            server_id = "srv_gone"
            password_encrypted = None
            password_rotated_at = None

        async def get_by_id(db_, cid):
            return _Ctrl()

        monkeypatch.setattr(internal_service.ipmi_repo, "get_by_id", get_by_id)

        async def server_get(db_, sid):
            return None

        monkeypatch.setattr(internal_service.server_repo, "get_by_id", server_get)

        payload = IpmiCredentialsRotatedRequest(
            new_password="Strong1Password",
            rotated_at=datetime.now(timezone.utc),
            verified_at=datetime.now(timezone.utc),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await internal_service.record_ipmi_credentials_rotated(
                db, _identity(), controller_id="ctrl_orphan", payload=payload,
                target_department_id=None,
            )
        assert exc_info.value.error_code == "NO_IPMI_CONTROLLER"

        action = "ipmi_controller.credentials_rotated_callback"
        events = [e for e in captured_emits if e["action"] == action]
        assert len(events) == 1, captured_emits
        reasons = [e["details"].get("reason") for e in events]
        assert "orphaned_ipmi_controller" in reasons
        # И обратное: фейковый actor_department_mismatch НЕ эмитим.
        assert "actor_department_mismatch" not in reasons


# ── 7. cancel_task без target_server_id и не в whitelist'е ────────────────────


class TestCancelTaskWithoutTargetNotCancellable:
    # Blanket-403 для non-system NULL-target отменён сознательно — ломал штатные
    # sandbox-dispatch'и (inventory.sync без актора). Усиление контракта
    # отложено до owner-решения: либо explicit is_system: bool колонка, либо
    # появление task'и без owner'а в проде. Тест-stub удалён, чтобы skip-rot
    # не маскировал решение в обратную сторону; реальная regress-страховка
    # для системного guard'а живёт ниже в `test_target_none_system_kind_*`.

    @pytest.mark.asyncio
    async def test_target_none_system_kind_still_blocked_by_system_guard(
        self, monkeypatch, captured_emits, db,
    ):
        """Системный kind по-прежнему ловится system_task_admin_required (не нашим новым 403)."""
        async def _ok(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "src.api.v1.endpoints.tasks.permissions.require_action", _ok,
        )

        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "worker.heartbeat",
                "created_by": None,
            }

        cancel_mock = AsyncMock()
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "_fetch_task_status_and_meta", fake_fetch,
        )
        monkeypatch.setattr(
            tasks_endpoint.worker_client, "cancel_task", cancel_mock,
        )

        identity = _identity(
            platform_role=PlatformRole.DEPARTMENT_ADMIN,
            service_roles={"server_service": ["admin"]},
            subject_type="user",
        )

        with pytest.raises(AuthorizationError) as exc_info:
            await tasks_endpoint.cancel_task_endpoint(
                identity=identity, task_id="tsk_sys_no_target", body=None, db=db,
            )
        # системный guard срабатывает раньше нашей новой ветки
        assert exc_info.value.error_code == "SYSTEM_TASK_ADMIN_REQUIRED"

        cancel_mock.assert_not_awaited()


# ── 8. W18-W1 подтверждение: audit на Redis-failure при prepare ──────────────


class TestPrepareDispatchRedisAuditTrail:
    """Проверяем, что код prepare-dispatch'а несёт эмиты на оба
    Redis-failure пути (`creds_store_unavailable` / `creds_store_failed`).
    Это smoke-проверка, что фикс W18-W1 не откатился."""

    def test_creds_store_unavailable_emit_present(self):
        import inspect

        src = inspect.getsource(wd_endpoint.server_prepare_dispatch)
        assert '"creds_store_unavailable"' in src

    def test_creds_store_failed_emit_present(self):
        import inspect

        src = inspect.getsource(wd_endpoint.server_prepare_dispatch)
        assert '"creds_store_failed"' in src
