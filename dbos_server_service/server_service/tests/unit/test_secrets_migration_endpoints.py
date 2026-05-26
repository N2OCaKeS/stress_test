"""Endpoint-контракт `/api/server/v1/internal/secrets/*`.

Проверяет authn/authz-gates, response shapes, hidden-from-OpenAPI инвариант
и идемпотентность ре-шифрации. Реальная KDF-логика покрывается отдельно
в `test_secrets_service.py` — здесь только endpoint surface.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1/internal/secrets"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
        assert set(body.keys()) == {"remaining", "total", "active_version", "by_version"}

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
