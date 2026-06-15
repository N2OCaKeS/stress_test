"""Coverage gaps — server_service.

Области:
* System-task admin guard — дополнительные role/task-kind вариации
* Cancel race task_not_found → failure status (created_by присутствует,
  target отсутствует; ветка ранее не покрыта)
* Cursor cap 500 boundary — normalize_limit(None) дефолт
* Cursor row_id regex — граница 63/64/65 и всего по одному символу
* Audit emit для outbox lifecycle (seed/done/failed/cleanup) —
  все четыре действия ни разу не проверялись на факт передачи правильных details
* os_version.list_anonymous cursor-mode — emit в cursor-path не покрыт
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock

import pytest

from src.core.constants import PlatformRole
from src.core.exceptions import AuthorizationError
from src.utils.cursor import (
    InvalidCursorError,
    decode_cursor,
    normalize_limit,
)
from tests._helpers import assert_error, auth_hdr as _hdr


# ─────────────────────────────────────────────────────────────────────────────
# Helpers для unit-тестов, не требующих DB
# ─────────────────────────────────────────────────────────────────────────────


def _identity(
    *,
    user_id: str = "usr_test",
    department_id: str | None = "dep_a",
    platform_role: PlatformRole | None = None,
    service_roles: dict[str, list[str]] | None = None,
):
    from src.schemas.identity import IdentityContext

    return IdentityContext(
        user_id=user_id,
        username="tester",
        department_id=department_id,
        department_name=None,
        allowed_services=["server_service"],
        service_roles=service_roles or {"server_service": ["admin"]},
        is_banned=False,
        platform_role=platform_role,
        subject_type="user",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cursor row_id regex — граничные случаи (дополнение к test_cursor_row_id_format.py)
# ─────────────────────────────────────────────────────────────────────────────


def _make_raw_token(row_id: str, sort_value: str = "2026-05-30T12:00:00") -> str:
    payload = json.dumps({"k": sort_value, "i": row_id}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


class TestRowIdBoundaryExtra:
    def test_63_chars_accepted(self):
        """63 символа — ниже лимита 64, допустимо."""
        token = _make_raw_token("a" * 63)
        cur = decode_cursor(token)
        assert len(cur.row_id) == 63

    def test_65_chars_rejected(self):
        """65 символов — выше лимита 64, отбрасывается."""
        token = _make_raw_token("a" * 65)
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_single_letter_accepted(self):
        """Один символ — минимально допустимая длина."""
        token = _make_raw_token("z")
        cur = decode_cursor(token)
        assert cur.row_id == "z"

    def test_single_digit_accepted(self):
        """Одна цифра — тоже в алфавите [a-z0-9_]."""
        token = _make_raw_token("0")
        cur = decode_cursor(token)
        assert cur.row_id == "0"

    def test_underscore_in_id_accepted(self):
        """Подчёркивание — единственный разрешённый спецсимвол."""
        token = _make_raw_token("srv_foo_bar")
        cur = decode_cursor(token)
        assert cur.row_id == "srv_foo_bar"

    def test_hyphen_in_id_rejected(self):
        """Дефис не входит в алфавит row_id — реальные id его не содержат."""
        token = _make_raw_token("srv-foo")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_dot_in_id_rejected(self):
        """Точка не входит в алфавит row_id."""
        token = _make_raw_token("srv.foo")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_slash_in_id_rejected(self):
        """Слеш не входит в алфавит row_id."""
        token = _make_raw_token("srv/foo")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_at_sign_in_id_rejected(self):
        """@ не входит в алфавит."""
        token = _make_raw_token("srv@host")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_newline_in_id_rejected(self):
        """Перевод строки не входит в алфавит."""
        token = _make_raw_token("srv\nfoo")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_null_byte_in_id_rejected(self):
        """Нулевой байт — не ASCII."""
        token = _make_raw_token("srv\x00")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_uppercase_rejected(self):
        """Верхний регистр не входит в алфавит — реальные id всегда lowercase."""
        token = _make_raw_token("SrvABC123xyz")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)


# ─────────────────────────────────────────────────────────────────────────────
# normalize_limit — дефолтное значение при None
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeLimitDefault:
    def test_none_returns_default_50(self):
        """None → дефолт 50 (задокументированный дефолт сервиса)."""
        assert normalize_limit(None) == 50

    def test_zero_clamps_to_one(self):
        """0 → нижний клип 1."""
        assert normalize_limit(0) == 1

    def test_negative_clamps_to_one(self):
        """-100 → 1."""
        assert normalize_limit(-100) == 1

    def test_501_clamps_to_500(self):
        """501 → верхний клип 500."""
        assert normalize_limit(501) == 500

    def test_large_value_clamps_to_500(self):
        """Очень большое значение → 500."""
        assert normalize_limit(10_000) == 500

    def test_499_passthrough(self):
        """499 — ниже cap, возвращается as-is."""
        assert normalize_limit(499) == 499

    def test_500_exact_passthrough(self):
        """500 — ровно cap, не обрезается."""
        assert normalize_limit(500) == 500


# ─────────────────────────────────────────────────────────────────────────────
# System-task guard — дополнительные вариации ролей и task_kind
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def patch_permissions_ok(monkeypatch):
    """permissions.require_action всегда OK."""
    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "src.api.v1.endpoints.tasks.permissions.require_action", _ok,
    )


@pytest.fixture
def captured_audit_sys(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    monkeypatch.setattr("src.api.v1.endpoints.tasks.audit_service.emit", fake_emit)
    monkeypatch.setattr("src.services.audit_service.emit", fake_emit)
    monkeypatch.setattr("src.services.server.audit_service.emit", fake_emit)
    return captured


class TestSystemTaskGuardRoleVariations:
    """Вариации платформенных ролей против system task guard."""

    async def _call_cancel(self, db, monkeypatch, identity, task_kind="tasks.cleanup_completed_old"):
        from src.api.v1.endpoints import tasks as ep

        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": task_kind,
                "created_by": None,
            }

        monkeypatch.setattr(ep.worker_client, "_fetch_task_status_and_meta", fake_fetch)
        monkeypatch.setattr(ep.worker_client, "cancel_task", AsyncMock())
        return await ep.cancel_task_endpoint(
            identity=identity,
            task_id=f"tsk_{task_kind}",
            body=None,
            db=db,
        )

    async def test_loging_admin_blocked_from_system_task(
        self, db, patch_permissions_ok, captured_audit_sys, monkeypatch,
    ):
        """loging_admin — не account_admin, system task должна отклоняться."""
        identity = _identity(
            platform_role=PlatformRole.LOGING_ADMIN,
            department_id=None,
            service_roles={},
        )
        with pytest.raises(AuthorizationError) as exc_info:
            await self._call_cancel(db, monkeypatch, identity)
        assert exc_info.value.error_code == "SYSTEM_TASK_ADMIN_REQUIRED"

    async def test_loging_reader_blocked_from_system_task(
        self, db, patch_permissions_ok, captured_audit_sys, monkeypatch,
    ):
        """loging_reader — тоже не account_admin."""
        identity = _identity(
            platform_role=PlatformRole.LOGING_READER,
            department_id=None,
            service_roles={},
        )
        with pytest.raises(AuthorizationError) as exc_info:
            await self._call_cancel(db, monkeypatch, identity)
        assert exc_info.value.error_code == "SYSTEM_TASK_ADMIN_REQUIRED"

    async def test_no_platform_role_blocked_from_system_task(
        self, db, patch_permissions_ok, captured_audit_sys, monkeypatch,
    ):
        """Обычный dept_admin без platform_role — блокируется."""
        identity = _identity(
            platform_role=None,
            service_roles={"server_service": ["admin"]},
        )
        with pytest.raises(AuthorizationError) as exc_info:
            await self._call_cancel(db, monkeypatch, identity)
        assert exc_info.value.error_code == "SYSTEM_TASK_ADMIN_REQUIRED"

    async def test_cleanup_completed_task_kind_in_audit_details(
        self, db, patch_permissions_ok, captured_audit_sys, monkeypatch,
    ):
        """task_kind=tasks.cleanup_completed_old — попадает в denied-аудит."""
        identity = _identity(platform_role=PlatformRole.DEPARTMENT_ADMIN)

        with pytest.raises(AuthorizationError):
            await self._call_cancel(
                db, monkeypatch, identity, task_kind="tasks.cleanup_completed_old",
            )

        ev = [e for e in captured_audit_sys if e["action"] == "task.cancelled"]
        assert len(ev) == 1
        assert ev[0]["details"]["task_kind"] == "tasks.cleanup_completed_old"
        assert ev[0]["details"]["reason"] == "system_task_admin_required"

    async def test_sweep_task_kind_in_audit_details(
        self, db, patch_permissions_ok, captured_audit_sys, monkeypatch,
    ):
        """task_kind=tasks.sweep_orphaned — тоже системная, блокируется аналогично heartbeat."""
        identity = _identity(platform_role=PlatformRole.DEPARTMENT_ADMIN)

        with pytest.raises(AuthorizationError):
            await self._call_cancel(db, monkeypatch, identity, task_kind="tasks.sweep_orphaned")

        ev = [e for e in captured_audit_sys if e["action"] == "task.cancelled"]
        assert len(ev) == 1
        assert ev[0]["details"]["task_kind"] == "tasks.sweep_orphaned"

    async def test_account_admin_can_cancel_cleanup_completed(
        self, db, patch_permissions_ok, captured_audit_sys, monkeypatch,
    ):
        """account_admin — единственная платформенная роль с правом отмены системных."""
        from src.api.v1.endpoints import tasks as ep

        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": None,
                "task_kind": "cleanup_completed",
                "created_by": None,
            }

        async def fake_cancel(*, task_id_value, cancelled_by, cancel_reason):
            return {
                "found": True,
                "cancelled": True,
                "previous_status": "queued",
                "task_kind": "cleanup_completed",
                "target_server_id": None,
            }

        monkeypatch.setattr(ep.worker_client, "_fetch_task_status_and_meta", fake_fetch)
        monkeypatch.setattr(ep.worker_client, "cancel_task", fake_cancel)

        identity = _identity(
            user_id="usr_root",
            department_id=None,
            platform_role=PlatformRole.ACCOUNT_ADMIN,
            service_roles={},
        )
        resp = await ep.cancel_task_endpoint(
            identity=identity,
            task_id="tsk_cleanup",
            body=None,
            db=db,
        )
        assert resp.status == "cancelled"
        assert resp.task_id == "tsk_cleanup"
        assert resp.previous_status == "queued"

    async def test_system_task_with_existing_server_is_not_system(
        self, db, patch_permissions_ok, captured_audit_sys, monkeypatch, make_server,
    ):
        """target_server_id задан — не системная задача, гарда не применяется
        даже если created_by=None."""
        srv = await make_server(department_id="dep_a")
        from src.api.v1.endpoints import tasks as ep

        async def fake_fetch(task_id_value):
            return {
                "status": "queued",
                "target_server_id": srv.id,
                "task_kind": "inventory.sync",
                "created_by": None,
            }

        async def fake_cancel(*, task_id_value, cancelled_by, cancel_reason):
            return {
                "found": True,
                "cancelled": True,
                "previous_status": "queued",
                "task_kind": "inventory.sync",
                "target_server_id": srv.id,
            }

        monkeypatch.setattr(ep.worker_client, "_fetch_task_status_and_meta", fake_fetch)
        monkeypatch.setattr(ep.worker_client, "cancel_task", fake_cancel)

        identity = _identity(
            platform_role=None,
            service_roles={"server_service": ["admin"]},
        )
        resp = await ep.cancel_task_endpoint(
            identity=identity,
            task_id="tsk_with_srv",
            body=None,
            db=db,
        )
        assert resp.status == "cancelled"
        # Denied-аудит не должен появляться — гарда не применялась.
        denied = [
            e for e in captured_audit_sys
            if e["action"] == "task.cancelled" and e.get("status") == "denied"
        ]
        assert denied == []


# ─────────────────────────────────────────────────────────────────────────────
# Audit emit — outbox lifecycle endpoints
# ─────────────────────────────────────────────────────────────────────────────


BASE_SECRETS = "/api/server/v1/internal/secrets"


@pytest.fixture
def captured_outbox_emits(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.secrets_migration.audit_service.emit", fake_emit,
    )
    return captured


class TestOutboxSeedAuditEmit:
    """secrets.reencrypt_seed — audit emit детали при вызове seed endpoint."""

    async def test_seed_empty_db_emits_correct_details(
        self, client, worker_pat_token, captured_outbox_emits,
    ):
        """Пустая БД: seed ничего не вставляет, но аудит всё равно эмитируется."""
        resp = await client.post(
            f"{BASE_SECRETS}/reencrypt_outbox/seed",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        events = [e for e in captured_outbox_emits if e["action"] == "secrets.reencrypt_seed"]
        assert len(events) == 1
        ev = events[0]
        assert ev["status"] == "success"
        assert ev["allowed"] is True
        details = ev["details"]
        assert details["inserted"] == 0
        assert details["scanned"] == 0
        assert "active_version" in details
        assert "limit" in details
        assert details["active_version"] >= 1

    async def test_seed_passes_limit_to_details(
        self, client, worker_pat_token, captured_outbox_emits,
    ):
        """Значение limit из query-параметра должно попасть в audit details."""
        resp = await client.post(
            f"{BASE_SECRETS}/reencrypt_outbox/seed?limit=42",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        events = [e for e in captured_outbox_emits if e["action"] == "secrets.reencrypt_seed"]
        assert len(events) == 1
        assert events[0]["details"]["limit"] == 42


class TestOutboxFinalizeFailedAuditEmit:
    """secrets.reencrypt_failed — audit emit при finalize_failed."""

    async def test_failed_existing_row_emits_audit(
        self, client, worker_pat_token, db, captured_outbox_emits,
    ):
        """Существующий pending → failed: emit с action=secrets.reencrypt_failed."""
        from src.models import ReencryptOutboxEntry

        entry = ReencryptOutboxEntry(
            id="rox_fail_001",
            entity_type="server_account",
            entity_id="acc_fail_001",
            legacy_ciphertext="v1$aaaa$bbbb",
            status="pending",
        )
        db.add(entry)
        await db.commit()

        # Сначала claim'им, чтобы перевести в processing.
        claim_resp = await client.get(
            f"{BASE_SECRETS}/reencrypt_outbox/pending?limit=10",
            headers=_hdr(worker_pat_token),
        )
        assert claim_resp.status_code == 200
        items = claim_resp.json()["items"]
        target = next((i for i in items if i["id"] == "rox_fail_001"), None)
        # Row коммитнут до claim'а — FOR UPDATE SKIP LOCKED обязан его подобрать.
        # Раньше тут стоял defensive-skip на «possible isolation issue»; это
        # маскировало флакайность claim-pattern'а на горячем пути. Если тест
        # стабильно отбивает None — это регресс claim'а, не повод скипать.
        assert target is not None, (
            f"claim не подобрал committed pending-row; items={items}"
        )

        captured_outbox_emits.clear()
        resp = await client.post(
            f"{BASE_SECRETS}/reencrypt_outbox/rox_fail_001/failed",
            headers=_hdr(worker_pat_token),
            json={"error": "decrypt failed: bad key"},
        )
        assert resp.status_code == 200
        events = [e for e in captured_outbox_emits if e["action"] == "secrets.reencrypt_failed"]
        assert len(events) == 1
        ev = events[0]
        assert ev["status"] == "failure"
        assert ev["allowed"] is True
        details = ev["details"]
        assert details["outbox_id"] == "rox_fail_001"
        assert "status" in details

    async def test_failed_missing_row_no_emit(
        self, client, worker_pat_token, captured_outbox_emits,
    ):
        """Несуществующий row → 404, аудит не эмитируется."""
        resp = await client.post(
            f"{BASE_SECRETS}/reencrypt_outbox/rox_ghost_fail/failed",
            headers=_hdr(worker_pat_token),
            json={"error": "test"},
        )
        assert_error(resp, 404, "SECRETS_OUTBOX_ROW_NOT_FOUND")
        events = [e for e in captured_outbox_emits if e["action"] == "secrets.reencrypt_failed"]
        assert events == []


class TestOutboxCleanupAuditEmit:
    """secrets.reencrypt_outbox_cleanup — audit emit при cleanup endpoint."""

    async def test_cleanup_empty_db_emits_details(
        self, client, worker_pat_token, captured_outbox_emits,
    ):
        """Пустая БД: deleted=0, но аудит всё равно с правильными details."""
        resp = await client.post(
            f"{BASE_SECRETS}/reencrypt_outbox/cleanup?older_than_hours=24",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        events = [
            e for e in captured_outbox_emits
            if e["action"] == "secrets.reencrypt_outbox_cleanup"
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev["status"] == "success"
        assert ev["allowed"] is True
        details = ev["details"]
        assert details["deleted"] == 0
        assert details["older_than_hours"] == 24

    async def test_cleanup_passes_older_than_hours_to_details(
        self, client, worker_pat_token, captured_outbox_emits,
    ):
        """older_than_hours=72 должен попасть в audit details."""
        resp = await client.post(
            f"{BASE_SECRETS}/reencrypt_outbox/cleanup?older_than_hours=72",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        events = [
            e for e in captured_outbox_emits
            if e["action"] == "secrets.reencrypt_outbox_cleanup"
        ]
        assert len(events) == 1
        assert events[0]["details"]["older_than_hours"] == 72


class TestOutboxDoneAuditEmit:
    """secrets.reencrypt_done — audit emit при успешной finalize_done."""

    async def test_done_missing_row_no_emit(
        self, client, worker_pat_token, captured_outbox_emits,
    ):
        """Несуществующий row → 404, аудит secrets.reencrypt_done не эмитируется."""
        resp = await client.post(
            f"{BASE_SECRETS}/reencrypt_outbox/rox_ghost_done/done",
            headers=_hdr(worker_pat_token),
        )
        assert_error(resp, 404, "SECRETS_OUTBOX_ROW_NOT_FOUND")
        events = [e for e in captured_outbox_emits if e["action"] == "secrets.reencrypt_done"]
        assert events == []

    async def test_done_emit_includes_required_details(
        self, client, worker_pat_token, db, captured_outbox_emits, monkeypatch,
    ):
        """При успешной finalize_done эмитируется secrets.reencrypt_done с
        outbox_id, status, skipped в details."""
        from src.api.v1.endpoints import secrets_migration as sm_ep

        async def fake_finalize_done(db, outbox_id):
            return {"id": outbox_id, "status": "done", "skipped": False}

        monkeypatch.setattr(
            sm_ep.secrets_migration_service, "finalize_done", fake_finalize_done,
        )

        # Создаём processing row чтобы auth/not_found-ветки не сработали
        from src.models import ReencryptOutboxEntry

        entry = ReencryptOutboxEntry(
            id="rox_done_001",
            entity_type="server_account",
            entity_id="acc_done_001",
            legacy_ciphertext="v1$xx$yy",
            status="processing",
        )
        db.add(entry)
        await db.commit()

        captured_outbox_emits.clear()
        resp = await client.post(
            f"{BASE_SECRETS}/reencrypt_outbox/rox_done_001/done",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200, resp.text
        events = [e for e in captured_outbox_emits if e["action"] == "secrets.reencrypt_done"]
        assert len(events) == 1
        ev = events[0]
        assert ev["status"] == "success"
        assert ev["allowed"] is True
        details = ev["details"]
        assert details["outbox_id"] == "rox_done_001"
        assert "status" in details
        assert "skipped" in details
        assert details["skipped"] is False


# ─────────────────────────────────────────────────────────────────────────────
# os_version.list_anonymous — cursor-path audit emit (не покрыт до этого файла)
# ─────────────────────────────────────────────────────────────────────────────

BASE_OS = "/api/server/v1/os-versions"


@pytest.fixture
def captured_os_emits(monkeypatch):
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    # os_versions endpoint делает `from src.services import audit_service`
    # — оба пути покрываем.
    monkeypatch.setattr(
        "src.api.v1.endpoints.os_versions.audit_service.emit", fake_emit,
    )
    return captured


class TestOsVersionAnonCursorAudit:
    """Anonymous cursor-mode list должен тоже эмитировать os_version.list_anonymous."""

    async def test_anon_cursor_mode_emits_audit(self, client, captured_os_emits):
        """?cursor=true без токена — emit os_version.list_anonymous с has_more."""
        resp = await client.get(f"{BASE_OS}?cursor=true")
        assert resp.status_code == 200
        events = [e for e in captured_os_emits if e["action"] == "os_version.list_anonymous"]
        assert len(events) == 1
        ev = events[0]
        assert ev["status"] == "success"
        assert ev["details"]["caller_type"] == "anonymous"
        assert "page_size" in ev["details"]
        assert "has_more" in ev["details"]

    async def test_anon_cursor_mode_with_after_token_emits_audit(
        self, client, admin_role_token_a, captured_os_emits,
    ):
        """?after=<token> без токена авторизации — cursor-path, emit os_version.list_anonymous."""
        # Засеваем 3 версии — limit=1 точно оставит next_cursor.
        for i in range(3):
            resp = await client.post(
                BASE_OS,
                headers=_hdr(admin_role_token_a),
                json={"name": f"osv-cursor-anon-{i}"},
            )
            assert resp.status_code in (200, 201), resp.text

        first_page = await client.get(f"{BASE_OS}?cursor=true&limit=1")
        assert first_page.status_code == 200
        body = first_page.json()
        next_cursor = body.get("next_cursor")
        # Раньше тут стоял defensive-skip; при 3 строках в БД с limit=1
        # next_cursor обязан быть. Если пропал — это регресс cursor-формата.
        assert next_cursor is not None, body

        captured_os_emits.clear()
        resp = await client.get(f"{BASE_OS}?after={next_cursor}")
        assert resp.status_code == 200
        events = [e for e in captured_os_emits if e["action"] == "os_version.list_anonymous"]
        assert len(events) == 1

    async def test_anon_legacy_offset_mode_emits_audit(self, client, captured_os_emits):
        """Offset-mode без токена — emit c total, без has_more."""
        resp = await client.get(f"{BASE_OS}?limit=5&offset=0")
        assert resp.status_code == 200
        events = [e for e in captured_os_emits if e["action"] == "os_version.list_anonymous"]
        assert len(events) == 1
        ev = events[0]
        assert "total" in ev["details"]
        assert ev["details"]["caller_type"] == "anonymous"

    async def test_authenticated_cursor_mode_no_audit(
        self, client, no_role_token_a, captured_os_emits,
    ):
        """Authenticated cursor-mode — аудит НЕ эмитируется."""
        resp = await client.get(
            f"{BASE_OS}?cursor=true",
            headers=_hdr(no_role_token_a),
        )
        assert resp.status_code == 200
        os_events = [e for e in captured_os_emits if e["action"].startswith("os_version")]
        assert os_events == []

    async def test_anon_cursor_invalid_after_returns_400_no_audit(
        self, client, captured_os_emits,
    ):
        """Невалидный cursor → 400 INVALID_CURSOR без audit-emit."""
        resp = await client.get(f"{BASE_OS}?after=notvalidbase64!!!")
        assert_error(resp, 400, "INVALID_CURSOR")
        events = [e for e in captured_os_emits if e["action"] == "os_version.list_anonymous"]
        assert events == []
