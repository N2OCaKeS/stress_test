"""Тесты бутстрапа управления сервером (#14).

Покрывает:
* trigger-dispatch `POST /servers/{id}/prepare` — 202 + task_id, право
  (update), base64-декод кред в payload, битый base64 → 422, decommissioned;
* callback `POST /internal/servers/{id}/prepared` — помечает сервер
  `is_managed`/`prepared_at`/`management_user`; worker_bot может, reader нет;
* bootstrap-креды не уходят в audit.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from src.core.constants import ServerStatus
from src.models import Server

BASE = "/api/server/v1/servers"
BASE_INT = "/api/server/v1/internal"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task из endpoints/worker_dispatch.py.

    Возвращает список dispatch-вызовов. Каждый элемент дополнительно несёт
    `stored_creds` — креды, которые prepare положил бы в Redis под ключ из
    payload (мокаем `store_prepare_creds`, чтобы тест не ходил в Redis).

    На fixture'е дополнительно висят:
    * `stored_creds_calls` — список (creds_key, creds) в порядке поступления,
      чтобы проверять, что `store_prepare_creds` не сработал ДО проверки прав;
    * `deleted_keys` — список creds_key, для которых был вызов
      `delete_prepare_creds` (cleanup на падении dispatch'а).
    """
    class _Recorder(list):
        """list-подкласс, поддерживающий атрибуты — для совместимости с
        существующими тестами, которые читают fixture как обычный список,
        и для новых тестов, которые проверяют side-effects store/delete."""

    calls: _Recorder = _Recorder()
    stored: dict[str, dict] = {}
    stored_calls: list[tuple[str, dict]] = []
    deleted: list[str] = []
    calls.stored_creds_calls = stored_calls  # type: ignore[attr-defined]
    calls.deleted_keys = deleted  # type: ignore[attr-defined]

    async def fake_store(creds_key, creds):
        stored[creds_key] = creds
        stored_calls.append((creds_key, creds))

    async def fake_delete(creds_key):
        deleted.append(creds_key)

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        creds_key = payload.get("bootstrap_creds_key")
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "payload": payload,
            "stored_creds": stored.get(creds_key) if creds_key else None,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if return_hit:
            return new_id, False
        return new_id

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "store_prepare_creds", fake_store)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.store_prepare_creds",
        fake_store,
    )
    monkeypatch.setattr(worker_mod, "delete_prepare_creds", fake_delete)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.delete_prepare_creds",
        fake_delete,
    )
    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


@pytest.fixture
def captured_emits_prepare(monkeypatch):
    """Захват `audit_service.emit` для prepare-эндпоинта."""
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
    )


# ── Trigger: POST /servers/{id}/prepare ──────────────────────────────────────


class TestPrepareDispatch:
    async def test_operator_prepares(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"].startswith("tsk_")
        assert body["status"] == "queued"
        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "server.prepare"
        # Plaintext-кред в payload НЕТ — только ссылка на Redis-ключ.
        assert "bootstrap_login" not in call["payload"]
        assert "bootstrap_password" not in call["payload"]
        creds_key = call["payload"]["bootstrap_creds_key"]
        assert creds_key.startswith("dbos:prepare_creds:")
        assert call["payload"]["target_department_id"] == "dep_a"
        # base64 декодирован и креды ушли в Redis-store под этот ключ.
        assert call["stored_creds"] == {
            "bootstrap_login": "bootadmin",
            "bootstrap_password": "Boot1234!StrongPwd",
        }

    async def test_reader_cannot_prepare(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(reader_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch == []

    async def test_bad_base64_username_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": "!!notb64!!", "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")
        assert captured_dispatch == []

    async def test_bad_base64_password_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": "@@bad@@"},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")
        assert captured_dispatch == []

    # ── Усиленная парольная политика для bootstrap-пароля ──────────────────

    async def test_weak_password_short_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        # Короткий пароль с буквой/цифрой/символом — отказ только по длине.
        srv = await make_server(department_id="dep_a")
        pwd = "Boot1234!Short"  # 14
        assert len(pwd) < 16
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64(pwd),
            },
        )
        body = assert_error(resp, 422, "VALIDATION_ERROR")
        types = [e["type"] for e in body["details"]["errors"]]
        assert "WEAK_PASSWORD" in types
        assert captured_dispatch.stored_creds_calls == []

    async def test_weak_password_exactly_15_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        # Ровно 15 символов с буквой/цифрой/символом — всё равно отказ.
        srv = await make_server(department_id="dep_a")
        pwd = "Aa1!Aa1!Aa1!Aa1"  # 15
        assert len(pwd) == 15
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64(pwd),
            },
        )
        body = assert_error(resp, 422, "VALIDATION_ERROR")
        types = [e["type"] for e in body["details"]["errors"]]
        assert "WEAK_PASSWORD" in types
        assert captured_dispatch == []

    async def test_weak_password_no_letter_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        # 16+ цифр и символов, но ни одной буквы.
        srv = await make_server(department_id="dep_a")
        pwd = "1234567890!@#$%^"  # 16
        assert len(pwd) == 16
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64(pwd),
            },
        )
        body = assert_error(resp, 422, "VALIDATION_ERROR")
        types = [e["type"] for e in body["details"]["errors"]]
        assert "WEAK_PASSWORD" in types
        assert captured_dispatch == []

    async def test_weak_password_no_digit_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        pwd = "AbcdEfghIjkl!@#$"  # 16, no digit
        assert len(pwd) == 16
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64(pwd),
            },
        )
        body = assert_error(resp, 422, "VALIDATION_ERROR")
        types = [e["type"] for e in body["details"]["errors"]]
        assert "WEAK_PASSWORD" in types
        assert captured_dispatch == []

    async def test_weak_password_no_symbol_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        pwd = "Abcd1234Efgh5678"  # 16, only alnum
        assert len(pwd) == 16
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64(pwd),
            },
        )
        body = assert_error(resp, 422, "VALIDATION_ERROR")
        types = [e["type"] for e in body["details"]["errors"]]
        assert "WEAK_PASSWORD" in types
        assert captured_dispatch == []

    async def test_strong_password_exactly_16_ok(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        # Граница длины: ровно 16, буква+цифра+символ — пропускаем.
        srv = await make_server(department_id="dep_a")
        pwd = "Aa1!Aa1!Aa1!Aa1!"  # 16
        assert len(pwd) == 16
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64(pwd),
            },
        )
        assert resp.status_code == 202, resp.text
        assert len(captured_dispatch) == 1

    async def test_cross_dept_server_404(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_b),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert captured_dispatch == []

    async def test_decommissioned_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")
        assert captured_dispatch == []

    # ── Bootstrap-кред НЕ кладутся в Redis до проверки прав/видимости ──

    async def test_reader_does_not_store_creds_in_redis(
        self, client, reader_token_a, make_server, captured_dispatch,
    ):
        # Reader без `update` не должен иметь возможности забить Redis
        # plaintext-блобами: store_prepare_creds обязан произойти ПОСЛЕ
        # permission-check, не раньше.
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(reader_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch.stored_creds_calls == []

    async def test_cross_dept_does_not_store_creds_in_redis(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_b),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")
        assert captured_dispatch.stored_creds_calls == []

    async def test_decommissioned_does_not_store_creds_in_redis(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 409, "SERVER_DECOMMISSIONED")
        assert captured_dispatch.stored_creds_calls == []

    async def test_idempotent_hit_does_not_store_creds_in_redis(
        self, client, operator_token_a, make_server, captured_dispatch,
        monkeypatch,
    ):
        """Повторный POST с тем же Idempotency-Key не должен класть второй
        plaintext-stash в Redis. До фикса `store_prepare_creds` срабатывал
        ДО idempotency-lookup'а, и каждый retry оставлял orphan-ключ с
        bootstrap-паролем, висящий до TTL.
        """
        srv = await make_server(department_id="dep_a")
        idem_key = "ik-prepare-replay"
        existing_task_id = "tsk_server_prepare_existing"

        # Первая попытка: ключа ещё нет, обычный путь. После неё _by_key
        # в fake_dispatch запомнит idem_key → task_id; но эндпоинт теперь
        # делает pre-check через `_get_task_by_idempotency_key`, который
        # бьётся в реальную worker-БД. Мочим оба пути одним стейтом.
        existing: dict[str, tuple[str, str, str | None]] = {}

        async def fake_lookup(key):
            return existing.get(key)

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "_get_task_by_idempotency_key", fake_lookup)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client._get_task_by_idempotency_key",
            fake_lookup,
        )

        # Первая постановка должна положить креды и попасть в dispatch.
        resp1 = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers={**_hdr(operator_token_a), "Idempotency-Key": idem_key},
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp1.status_code == 202, resp1.text
        first_task_id = resp1.json()["task_id"]
        assert len(captured_dispatch.stored_creds_calls) == 1

        # Симулируем что row создан и `_get_task_by_idempotency_key` теперь
        # отдаёт его. Используем тот же task_id, что вернул endpoint.
        existing[idem_key] = (first_task_id, "server.prepare", srv.id)

        # Второй POST с тем же ключом — idempotent replay. Эндпоинт обязан
        # вернуть тот же task_id и НЕ положить второй creds-stash в Redis.
        resp2 = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers={**_hdr(operator_token_a), "Idempotency-Key": idem_key},
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp2.status_code == 202, resp2.text
        assert resp2.json()["task_id"] == first_task_id
        # Главное: stored_creds_calls остался 1 — replay не плодит plaintext.
        assert len(captured_dispatch.stored_creds_calls) == 1
        # И dispatch_task на replay'е тоже не дёргался — он отбит pre-check'ом.
        # captured_dispatch обновляется только при реальном вызове fake_dispatch.
        assert len(captured_dispatch) == 1

    async def test_prepare_uses_public_lookup_existing_task(
        self, client, operator_token_a, make_server, captured_dispatch,
        monkeypatch,
    ):
        """Prepare-эндпоинт идёт через публичный `lookup_existing_task`.

        Симметрия с остальными dispatch'ами (`_dispatch_for_server`). Если
        внутрь worker_client'а добавится новый guard в `lookup_existing_task`
        (например, дополнительная валидация), prepare обязан его подхватить
        автоматически, без точечной правки в endpoint'е.
        """
        srv = await make_server(department_id="dep_a")
        idem_key = "ik-lookup-symmetry"
        first_task_id = "tsk_server_prepare_via_lookup"
        existing: dict[str, tuple[str, str, str | None]] = {}
        lookup_calls: list[str] = []

        async def fake_lookup(key):
            lookup_calls.append(key)
            return existing.get(key)

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "lookup_existing_task", fake_lookup)

        resp1 = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers={**_hdr(operator_token_a), "Idempotency-Key": idem_key},
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp1.status_code == 202, resp1.text
        # Эндпоинт обязан был вызвать публичный lookup_existing_task —
        # не приватный _get_task_by_idempotency_key.
        assert lookup_calls == [idem_key]
        first_task_id = resp1.json()["task_id"]

        # Replay-hit: lookup отдаёт существующий task — endpoint возвращает
        # тот же task_id без второго dispatch'а.
        existing[idem_key] = (first_task_id, "server.prepare", srv.id)
        resp2 = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers={**_hdr(operator_token_a), "Idempotency-Key": idem_key},
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp2.status_code == 202, resp2.text
        assert resp2.json()["task_id"] == first_task_id
        assert lookup_calls == [idem_key, idem_key]

    async def test_idempotent_hit_with_kind_mismatch_emits_failure(
        self, client, operator_token_a, make_server, captured_dispatch,
        captured_emits_prepare, monkeypatch,
    ):
        """Re-use Idempotency-Key под другим task_kind — 409
        IDEMPOTENCY_KEY_REUSE_CONFLICT, без store_prepare_creds."""
        srv = await make_server(department_id="dep_a")
        idem_key = "ik-conflict"

        async def fake_lookup(key):
            if key == idem_key:
                return ("tsk_other_kind", "inventory.sync", srv.id)
            return None

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "_get_task_by_idempotency_key", fake_lookup)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client._get_task_by_idempotency_key",
            fake_lookup,
        )

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers={**_hdr(operator_token_a), "Idempotency-Key": idem_key},
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert_error(resp, 409, "IDEMPOTENCY_KEY_REUSE_CONFLICT")
        # Креды не легли в Redis — pre-check сработал ДО store_prepare_creds.
        assert captured_dispatch.stored_creds_calls == []
        failures = [
            e for e in captured_emits_prepare
            if e.get("action") == "server.prepare"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "idempotency_key_reuse_conflict"

    async def test_dispatch_failure_cleans_up_creds_in_redis(
        self, client, operator_token_a, make_server, captured_dispatch,
        monkeypatch,
    ):
        # Если worker недоступен (ServiceUnavailable), уже положенные в Redis
        # plaintext-кред'ы должны быть удалены — иначе висят до TTL=900s.
        from src.core.exceptions import ServiceUnavailableError

        async def boom(*args, **kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE",
                message="redis down",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "dispatch_task", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )
        monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            boom,
        )

        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp.status_code == 503, resp.text
        # Кред'ы успели лечь под некий ключ — этот же ключ обязан попасть
        # в delete_prepare_creds.
        assert len(captured_dispatch.stored_creds_calls) == 1
        creds_key, _ = captured_dispatch.stored_creds_calls[0]
        assert creds_key in captured_dispatch.deleted_keys

    async def test_store_creds_runtime_failure_503_with_audit(
        self, client, operator_token_a, make_server,
        captured_dispatch, captured_emits_prepare, monkeypatch,
    ):
        """Redis-runtime фейл в store_prepare_creds (timeout/connect refused)
        не должен утекать наружу как голый 500 без аудита.

        Endpoint обязан поймать → emit failure → отдать 503
        WORKER_REDIS_UNAVAILABLE. Иначе атакующий, который ронит Redis-связь,
        получает execute-path без следов в журнале.
        """
        async def boom(*args, **kwargs):
            raise RuntimeError("redis connection refused")

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "store_prepare_creds", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.store_prepare_creds",
            boom,
        )

        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["error_code"] == "WORKER_REDIS_UNAVAILABLE"
        # Dispatch до воркера не дошёл.
        assert captured_dispatch == []
        # Audit failure записан, plaintext в деталях нет.
        failures = [
            e for e in captured_emits_prepare
            if e.get("action") == "server.prepare"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        details = failures[0]["details"]
        assert details["reason"] == "creds_store_failed"
        assert details["error_class"] == "RuntimeError"
        # Никакого plaintext-парольа / логина в audit-деталях.
        flat = repr(details)
        assert "bootadmin" not in flat
        assert "Boot1234" not in flat

    async def test_store_creds_service_unavailable_keeps_audit(
        self, client, operator_token_a, make_server,
        captured_dispatch, captured_emits_prepare, monkeypatch,
    ):
        """Если store_prepare_creds сам кинул ServiceUnavailableError
        (WORKER_REDIS_NOT_CONFIGURED) — пробрасываем оригинальную ошибку
        и обязательно эмитим audit failure (раньше эмита не было)."""
        from src.core.exceptions import ServiceUnavailableError

        async def boom(*args, **kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_REDIS_NOT_CONFIGURED",
                message="SERVER_WORKER_REDIS_URL is not set",
            )

        import src.services.worker_client as worker_mod
        monkeypatch.setattr(worker_mod, "store_prepare_creds", boom)
        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.store_prepare_creds",
            boom,
        )

        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["error_code"] == "WORKER_REDIS_NOT_CONFIGURED"
        failures = [
            e for e in captured_emits_prepare
            if e.get("action") == "server.prepare"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "creds_store_unavailable"

    # ── Опциональный SSH-ключ в ручном режиме ─────────────────────────────

    async def test_manual_mode_with_ssh_private_key(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
                "ssh_private_key_b64": _b64("-----BEGIN KEY-----\nabc\n-----END KEY-----"),
            },
        )
        assert resp.status_code == 202, resp.text
        call = captured_dispatch[0]
        assert call["stored_creds"] == {
            "bootstrap_login": "bootadmin",
            "bootstrap_password": "Boot1234!StrongPwd",
            "bootstrap_ssh_private_key": "-----BEGIN KEY-----\nabc\n-----END KEY-----",
        }

    # ── Валидация режима ──────────────────────────────────────────────────

    async def test_both_modes_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """account_id вместе с ручными полями → 422, ничего не диспатчится."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={
                "account_id": "acc_whatever",
                "username_b64": _b64("bootadmin"),
                "password_b64": _b64("Boot1234!StrongPwd"),
            },
        )
        assert_error(resp, 422, "VALIDATION_ERROR")
        assert captured_dispatch.stored_creds_calls == []

    async def test_no_mode_422(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Пустое тело (ни account_id, ни ручных полей) → 422."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")
        assert captured_dispatch.stored_creds_calls == []

    # ── Account-режим ─────────────────────────────────────────────────────

    async def test_account_mode_resolves_password(
        self, client, admin_role_token_a, make_server, make_account,
        captured_dispatch,
    ):
        """`{account_id}` — server_service сам достаёт пароль аккаунта в Redis."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, login="dbadmin",
            password="LinkedAcct-Pwd-1!",
        )
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(admin_role_token_a),
            json={"account_id": acc.id},
        )
        assert resp.status_code == 202, resp.text
        call = captured_dispatch[0]
        assert call["task_kind"] == "server.prepare"
        # Логин и расшифрованный пароль аккаунта ушли в Redis-stash.
        assert call["stored_creds"]["bootstrap_login"] == "dbadmin"
        assert call["stored_creds"]["bootstrap_password"] == "LinkedAcct-Pwd-1!"
        # Привязанный аккаунт уехал в stash полем `linked_accounts` — worker
        # заведёт его на сервере сразу на этапе prepare.
        linked = call["stored_creds"]["linked_accounts"]
        assert len(linked) == 1
        assert linked[0]["login"] == "dbadmin"
        assert linked[0]["password"] == "LinkedAcct-Pwd-1!"
        # Каждый аккаунт несёт свой свежесгенерированный ssh-keypair.
        assert linked[0]["ssh_public_key"]
        assert linked[0]["ssh_private_key"]
        # Пароль не уехал в payload.
        assert "bootstrap_password" not in call["payload"]
        assert "linked_accounts" not in call["payload"]

    async def test_account_mode_requires_view_password(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        """operator без `view_password` не может резолвить аккаунт-креды → 403."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="dbadmin")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"account_id": acc.id},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")
        assert captured_dispatch.stored_creds_calls == []

    async def test_account_mode_not_linked_404(
        self, client, admin_role_token_a, make_server, make_account,
        captured_dispatch,
    ):
        """account_id привязан к ДРУГОМУ серверу того же отдела → 404 ACCOUNT_NOT_LINKED."""
        srv = await make_server(department_id="dep_a")
        other = await make_server(department_id="dep_a")
        acc = await make_account(server_id=other.id, login="dbadmin")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(admin_role_token_a),
            json={"account_id": acc.id},
        )
        assert_error(resp, 404, "ACCOUNT_NOT_LINKED")
        assert captured_dispatch.stored_creds_calls == []

    async def test_account_mode_no_password_409(
        self, client, admin_role_token_a, make_server, make_account,
        captured_dispatch,
    ):
        """Привязанный аккаунт без сохранённого пароля → 409 ACCOUNT_HAS_NO_PASSWORD."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="dbadmin", password=None)
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(admin_role_token_a),
            json={"account_id": acc.id},
        )
        assert_error(resp, 409, "ACCOUNT_HAS_NO_PASSWORD")
        assert captured_dispatch.stored_creds_calls == []


# ── Callback: POST /internal/servers/{id}/prepared ───────────────────────────


@pytest.mark.usefixtures("soft_dept_mode")
class TestPreparedCallback:
    async def test_marks_server_managed(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        assert srv.is_managed is False

        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(worker_bot_token_a),
            json={"management_user": "dbos"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_managed"] is True
        assert body["prepared_at"] is not None

        await db.commit()
        refreshed = (await db.execute(
            select(Server).where(Server.id == srv.id)
        )).scalar_one()
        assert refreshed.is_managed is True
        assert refreshed.management_user == "dbos"
        assert refreshed.prepared_at is not None

    async def test_idempotent_repeat(
        self, client, worker_bot_token_a, make_server, db, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        for _ in range(2):
            resp = await client.post(
                f"{BASE_INT}/servers/{srv.id}/prepared",
                headers=_hdr(worker_bot_token_a),
                json={"management_user": "dbos"},
            )
            assert resp.status_code == 200, resp.text

    async def test_reader_cannot_callback(
        self, client, reader_token_a, make_server, dept_a,
    ):
        srv = await make_server(department_id=dept_a)
        resp = await client.post(
            f"{BASE_INT}/servers/{srv.id}/prepared",
            headers=_hdr(reader_token_a),
            json={"management_user": "dbos"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_unknown_server_404(
        self, client, worker_bot_token_a, dept_a,
    ):
        resp = await client.post(
            f"{BASE_INT}/servers/srv_does_not_exist/prepared",
            headers=_hdr(worker_bot_token_a),
            json={"management_user": "dbos"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")


# ── Audit: bootstrap-креды не уходят в журнал ────────────────────────────────


class TestPrepareAuditSafety:
    async def test_credentials_not_in_audit(
        self, client, operator_token_a, make_server, captured_dispatch, monkeypatch,
    ):
        events: list[dict] = []

        def fake_emit(action, **kwargs):
            events.append({"action": action, **kwargs})

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.audit_service.emit", fake_emit,
        )
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare",
            headers=_hdr(operator_token_a),
            json={"username_b64": _b64("bootadmin"), "password_b64": _b64("Boot1234!StrongPwd")},
        )
        assert resp.status_code == 202, resp.text
        blob = str(events)
        assert "Boot1234!StrongPwd" not in blob
        assert "bootadmin" not in blob
