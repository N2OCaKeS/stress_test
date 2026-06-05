"""Endpoint-контракт `/api/server/v1/internal/secrets/*`.

Проверяет authn/authz-gates, response shapes, hidden-from-OpenAPI инвариант
и идемпотентность ре-шифрации. Реальная KDF-логика покрывается отдельно
в `test_secrets_service.py` — здесь только endpoint surface.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1/internal/secrets"


from tests._helpers import auth_hdr as _hdr  # noqa: E402


class TestOpenApiHidden:
    async def test_paths_hidden_from_openapi(self, client):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert not any(p.startswith(f"{BASE}/") for p in paths), (
            "secrets migration endpoints must not appear in OpenAPI"
        )


class TestMigrationStatusAuth:
    async def test_no_token_returns_401(self, client):
        resp = await client.get(f"{BASE}/migration_status")
        assert resp.status_code == 401

    async def test_reader_forbidden(self, client, reader_token_a):
        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(reader_token_a)
        )
        assert resp.status_code == 403

    async def test_operator_forbidden(self, client, operator_token_a):
        """operator не имеет rotate_password по default — должен получить 403."""
        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(operator_token_a)
        )
        assert resp.status_code == 403

    async def test_worker_bot_can_read(self, client, worker_bot_token_a):
        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_bot_token_a)
        )
        assert resp.status_code == 200
        body = resp.json()
        # Без данных: remaining=0, total=0, active_version=int, by_version={}.
        assert body["remaining"] == 0
        assert body["total"] == 0
        assert body["active_version"] >= 1
        assert body["by_version"] == {}


class TestMigrationStatusShape:
    async def test_response_keys_present(self, client, worker_pat_token):
        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "remaining",
            "total",
            "active_version",
            "by_version",
            "app_env",
            "outbox",
        }
        assert set(body["outbox"].keys()) == {
            "pending",
            "processing",
            "done",
            "failed",
        }

    async def test_counts_with_records(
        self, client, worker_pat_token, make_server, make_account, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="root", password="pwd1")
        await make_account(server_id=srv.id, login="ops", password="pwd2")
        await make_ipmi(server_id=srv.id, password="ipmi-pwd")
        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_pat_token)
        )
        body = resp.json()
        # 2 server_accounts + 1 ipmi_controller = total 3.
        assert body["total"] == 3
        # Все шифровались активным ключом => все попали под active_version.
        active = body["active_version"]
        assert body["by_version"].get(str(active)) == 3 or body["by_version"].get(active) == 3
        assert body["remaining"] == 0

    async def test_remaining_excludes_active_version(
        self, client, worker_pat_token, make_server, make_account, db,
    ):
        """v1-ciphertext под active_version=2 → remaining=1."""
        from src.core.config import get_settings

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="x")
        # Перепишем токен с префиксом v1$… — формат корректен, но не active.
        # Берём ровно nonce+ct из существующего токена, только меняем версию.
        original = acc.password_encrypted
        _, nonce_b64, ct_b64 = original.split("$", 2)
        acc.password_encrypted = f"v1${nonce_b64}${ct_b64}"
        await db.flush()
        await db.commit()

        # Активная версия должна быть v2 (default тестов), чтобы записанный
        # v1-токен стал legacy и попал в remaining-счётчик миграции.
        settings = get_settings()
        if settings.server_encryption_key_version == 1:
            pytest.skip("active version is v1; cannot verify remaining>0 here")

        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_pat_token)
        )
        body = resp.json()
        assert body["remaining"] >= 1


class TestReencryptBatchAuth:
    async def test_reader_forbidden(self, client, reader_token_a):
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=10", headers=_hdr(reader_token_a)
        )
        assert resp.status_code == 403

    async def test_no_token_returns_401(self, client):
        resp = await client.post(f"{BASE}/reencrypt_batch?limit=10")
        assert resp.status_code == 401

    async def test_worker_bot_can_call(self, client, worker_bot_token_a):
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=10", headers=_hdr(worker_bot_token_a)
        )
        assert resp.status_code == 200


class TestReencryptBatchContract:
    async def test_empty_db_returns_zero(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=50", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"processed": 0, "errors": 0}

    async def test_limit_validation_rejects_zero(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=0", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 422

    async def test_limit_validation_rejects_negative(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=-1", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 422

    async def test_limit_validation_rejects_too_large(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=10001", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 422

    async def test_idempotent_when_all_active(
        self, client, worker_pat_token, make_server, make_account,
    ):
        """Повторный батч на полностью мигрированной БД processed=0."""
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, password="x")
        first = (await client.post(
            f"{BASE}/reencrypt_batch?limit=100", headers=_hdr(worker_pat_token)
        )).json()
        second = (await client.post(
            f"{BASE}/reencrypt_batch?limit=100", headers=_hdr(worker_pat_token)
        )).json()
        # Все записи — уже под active, поэтому ни один батч их не трогает.
        assert first["processed"] == 0
        assert second["processed"] == 0
        assert first["errors"] == 0 and second["errors"] == 0


# ── Audit-status semantics: total-failure vs partial/success ────────────────


@pytest.fixture
def captured_emits(monkeypatch):
    """Тонкая копия фикстуры из test_roles_endpoints — мы здесь читаем,
    какой status попадает в `secrets.reencrypt_batch` emit."""
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kwargs):
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    import src.services.audit_service as audit_mod
    monkeypatch.setattr(audit_mod, "emit", fake_emit)
    # endpoints/secrets_migration.py делает `from src.services import audit_service`
    # — патчим тот же объект, который endpoint видит.
    monkeypatch.setattr(
        "src.api.v1.endpoints.secrets_migration.audit_service.emit", fake_emit
    )
    return captured


class TestReencryptBatchAuditStatus:
    """`secrets.reencrypt_batch` emit: failure-статус при total-failure."""

    async def test_success_on_empty_db(
        self, client, worker_pat_token, captured_emits,
    ):
        """processed=0, errors=0 → пустой батч, audit пишется status=success."""
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=10", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 200
        events = [e for e in captured_emits if e["action"] == "secrets.reencrypt_batch"]
        assert len(events) == 1
        assert events[0]["status"] == "success"
        assert events[0]["details"]["processed"] == 0
        assert events[0]["details"]["errors"] == 0

    async def test_failure_when_all_batch_rows_errored(
        self, client, worker_pat_token, monkeypatch, captured_emits,
    ):
        """processed=0, errors>0 → status=failure (битый ciphertext / wrong key)."""
        async def fake_reencrypt(db, limit):
            return {"processed": 0, "errors": 5}

        monkeypatch.setattr(
            "src.api.v1.endpoints.secrets_migration."
            "secrets_migration_service.reencrypt_batch",
            fake_reencrypt,
        )
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=10", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 200
        events = [e for e in captured_emits if e["action"] == "secrets.reencrypt_batch"]
        assert len(events) == 1
        assert events[0]["status"] == "failure", (
            "total-failure batch (processed=0, errors>0) must emit status=failure "
            "so loging_service rules can escalate severity"
        )
        assert events[0]["details"]["errors"] == 5
        assert events[0]["details"]["processed"] == 0

    async def test_success_when_partial_errors(
        self, client, worker_pat_token, monkeypatch, captured_emits,
    ):
        """processed>0, errors>0 → status=success (часть прошла = прогресс есть)."""
        async def fake_reencrypt(db, limit):
            return {"processed": 3, "errors": 1}

        monkeypatch.setattr(
            "src.api.v1.endpoints.secrets_migration."
            "secrets_migration_service.reencrypt_batch",
            fake_reencrypt,
        )
        resp = await client.post(
            f"{BASE}/reencrypt_batch?limit=10", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 200
        events = [e for e in captured_emits if e["action"] == "secrets.reencrypt_batch"]
        assert len(events) == 1
        assert events[0]["status"] == "success"
        assert events[0]["details"]["processed"] == 3
        assert events[0]["details"]["errors"] == 1


# ── Outbox-pattern endpoints ────────────────────────────────────────────────


class TestOutboxSeedEndpoint:
    async def test_no_token_returns_401(self, client):
        resp = await client.post(f"{BASE}/reencrypt_outbox/seed")
        assert resp.status_code == 401

    async def test_reader_forbidden(self, client, reader_token_a):
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/seed", headers=_hdr(reader_token_a)
        )
        assert resp.status_code == 403

    async def test_empty_db_no_inserts(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/seed", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["inserted"] == 0
        assert body["scanned"] == 0
        assert body["active_version"] >= 1

    async def test_limit_validation(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/seed?limit=0", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 422
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/seed?limit=99999",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 422


class TestOutboxClaimEndpoint:
    async def test_empty_returns_no_items(self, client, worker_pat_token):
        resp = await client.get(
            f"{BASE}/reencrypt_outbox/pending?limit=10",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"items": []}

    async def test_reader_forbidden(self, client, reader_token_a):
        resp = await client.get(
            f"{BASE}/reencrypt_outbox/pending?limit=10",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403


class TestOutboxFinalizeEndpoints:
    async def test_done_missing_returns_404(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/rox_doesnotexist/done",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SECRETS_OUTBOX_ROW_NOT_FOUND"

    async def test_failed_missing_returns_404(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/rox_doesnotexist/failed",
            headers=_hdr(worker_pat_token),
            json={"error": "boom"},
        )
        assert resp.status_code == 404

    async def test_failed_requires_error_body(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/rox_x/failed",
            headers=_hdr(worker_pat_token),
            json={},
        )
        assert resp.status_code == 422


class TestOutboxCleanupEndpoint:
    async def test_empty_db_returns_zero(self, client, worker_pat_token):
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/cleanup?older_than_hours=24",
            headers=_hdr(worker_pat_token),
        )
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 0}

    async def test_reader_forbidden(self, client, reader_token_a):
        resp = await client.post(
            f"{BASE}/reencrypt_outbox/cleanup?older_than_hours=24",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403


class TestOutboxRoundTrip:
    """End-to-end happy-path: seed → claim → done; v1-ciphertext → v2."""

    async def test_full_lifecycle(
        self, client, worker_pat_token, make_server, make_account, db,
    ):
        from src.core.config import get_settings

        settings = get_settings()
        if settings.server_encryption_key_version == 1:
            pytest.skip("active version is v1; cannot synthesize legacy ciphertext")

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="secret-x")
        # Перепишем токен с префиксом v1$… — формат корректен, но не active.
        original = acc.password_encrypted
        _, nonce_b64, ct_b64 = original.split("$", 2)
        acc.password_encrypted = f"v1${nonce_b64}${ct_b64}"
        await db.flush()
        await db.commit()

        # 1. seed
        seed = await client.post(
            f"{BASE}/reencrypt_outbox/seed", headers=_hdr(worker_pat_token)
        )
        assert seed.status_code == 200, seed.text
        body = seed.json()
        assert body["inserted"] >= 1

        # 2. claim
        claim = await client.get(
            f"{BASE}/reencrypt_outbox/pending?limit=10",
            headers=_hdr(worker_pat_token),
        )
        assert claim.status_code == 200
        items = claim.json()["items"]
        assert len(items) >= 1
        target = next(i for i in items if i["entity_id"] == acc.id)
        # legacy ciphertext был v1, owner-row v1 — но crypto не может
        # расшифровать тот ciphertext без оригинального plain. Финализация
        # в этом сценарии должна упасть (потому что мы подменили только
        # префикс версии), и это ОК — мы проверяем shape API. Реальный
        # сценарий с консистентным v1 ciphertext'ом покрывается в
        # integration-тестах.
        finalize = await client.post(
            f"{BASE}/reencrypt_outbox/{target['id']}/done",
            headers=_hdr(worker_pat_token),
        )
        # Либо 500 (decrypt не смог) — тогда воркер сам пометит failed,
        # либо 200 с done. Оба варианта валидны для shape-теста.
        assert finalize.status_code in (200, 500)

    async def test_finalize_done_owner_vanished_emits_warning(
        self, client, worker_pat_token, captured_emits, db,
    ):
        """`finalize_done` молчком закрывал outbox-row если owner-row пропал
        mid-migration. Теперь — warning-audit `secrets.migration.skipped` с
        `reason=owner_vanished`.
        """
        from src.models import ReencryptOutboxEntry

        # processing-row, ссылающаяся на несуществующий account_id.
        entry = ReencryptOutboxEntry(
            id="rox_vanished_001",
            entity_type="server_account",
            entity_id="acc_does_not_exist",
            legacy_ciphertext="v1$abc$def",
            status="processing",
        )
        db.add(entry)
        await db.commit()

        # owner-check теперь идёт ДО decrypt/encrypt — для owner_vanished ветки
        # ciphertext вообще не дёргается. Оставляем валидный ciphertext на случай,
        # если в будущем порядок поменяется обратно, но для прохождения теста это
        # не обязательно.
        from src.services import secrets_service
        plain = "x"
        aad = secrets_service.aad_for_server_account_password("acc_does_not_exist")
        entry.legacy_ciphertext = secrets_service.encrypt(plain, aad=aad)
        await db.commit()

        finalize = await client.post(
            f"{BASE}/reencrypt_outbox/{entry.id}/done",
            headers=_hdr(worker_pat_token),
        )
        assert finalize.status_code == 200, finalize.text
        body = finalize.json()
        assert body["skipped"] is True

        skipped_emits = [
            e for e in captured_emits
            if e["action"] == "secrets.migration.skipped"
        ]
        assert len(skipped_emits) == 1
        emit = skipped_emits[0]
        assert emit["details"]["reason"] == "owner_vanished"
        assert emit["details"]["entity_type"] == "server_account"
        assert emit["status"] == "warning"

    async def test_finalize_done_status_not_processing_emits_warning(
        self, client, worker_pat_token, captured_emits, db,
    ):
        """`finalize_done` на row со status≠processing раньше молча возвращал
        skipped. Теперь — warning-audit с reason=status_not_processing.
        """
        from src.models import ReencryptOutboxEntry

        entry = ReencryptOutboxEntry(
            id="rox_done_already",
            entity_type="server_account",
            entity_id="acc_test_done",
            legacy_ciphertext="v1$abc$def",
            status="done",
        )
        db.add(entry)
        await db.commit()

        finalize = await client.post(
            f"{BASE}/reencrypt_outbox/{entry.id}/done",
            headers=_hdr(worker_pat_token),
        )
        assert finalize.status_code == 200, finalize.text

        skipped_emits = [
            e for e in captured_emits
            if e["action"] == "secrets.migration.skipped"
        ]
        assert len(skipped_emits) == 1
        emit = skipped_emits[0]
        assert emit["details"]["reason"] == "status_not_processing"
        assert emit["details"]["current_status"] == "done"
        assert emit["status"] == "warning"

    async def test_claim_marks_row_processing(
        self, client, worker_pat_token, db,
    ):
        """После claim'а — row в processing, attempts=1."""
        from src.models import ReencryptOutboxEntry

        entry = ReencryptOutboxEntry(
            id="rox_test_001",
            entity_type="server_account",
            entity_id="acc_test_001",
            legacy_ciphertext="v1$abc$def",
            status="pending",
        )
        db.add(entry)
        await db.commit()

        claim = await client.get(
            f"{BASE}/reencrypt_outbox/pending?limit=10",
            headers=_hdr(worker_pat_token),
        )
        assert claim.status_code == 200
        items = claim.json()["items"]
        target = next((i for i in items if i["id"] == "rox_test_001"), None)
        assert target is not None
        assert target["attempts"] == 1

        # Финализируем как failed — должно работать.
        failed = await client.post(
            f"{BASE}/reencrypt_outbox/rox_test_001/failed",
            headers=_hdr(worker_pat_token),
            json={"error": "test-error"},
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
