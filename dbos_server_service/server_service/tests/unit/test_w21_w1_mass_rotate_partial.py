"""Mass-rotation partial-failure: структурный ответ + audit `mass_rotation.partial_failure`.

Owner W21-W1: при ServiceUnavailableError на K-м сервере в mode=all (mass
rotate) больше НЕ отбиваем 503. Возвращаем 202 со structured response:

* `partial_failure: True`
* `next_action: "manual_cancel_dispatched"`
* `tasks`: уже dispatched task_ids (для ручной отмены через POST `/tasks/{id}/cancel`)
* `skipped`: failed-сервер + остаток (`not_attempted`)

И эмитим WARNING-event `mass_rotation.partial_failure` с тем же составом
для SIEM. Auto-cancel НЕ выполняется (риск частичных откатов на боксах).
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"


from tests._helpers import auth_hdr as _hdr, make_emit_capture  # noqa: E402


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.worker_dispatch.audit_service.emit",
        "src.services.server.audit_service.emit",
    )


class TestMassRotatePartialFailureResponse:
    """Сценарий: 3 сервера, на 2-м воркер бросает ServiceUnavailable."""

    async def test_partial_failure_returns_structured_202(
        self, client, operator_token_a, make_server, make_account,
        captured_emits, monkeypatch,
    ):
        from src.core.exceptions import ServiceUnavailableError

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        srv3 = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv1.id, srv2.id, srv3.id], login="ops",
        )
        # `linked_server_ids` сортирует по (created_at, server_id); при bulk
        # INSERT created_at совпадает, поэтому итерация идёт по lex(server_id).
        sorted_ids = sorted([srv1.id, srv2.id, srv3.id])

        call_count = {"n": 0}

        async def dispatch_then_boom(*, target_server_id, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ("tsk_dispatched_1", False)
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE",
                message="redis dropped",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            dispatch_then_boom,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        # 202, не 503: mass-режим с хотя бы одним успешным dispatch'ем.
        assert resp.status_code == 202, resp.text
        body = resp.json()

        # Response shape: новые поля для UX.
        assert body["mode"] == "all"
        assert body["status"] == "partial"
        assert body["partial_failure"] is True
        assert body["next_action"] == "manual_cancel_dispatched"

        # Один task_id в response — для cancel UI.
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["task_id"] == "tsk_dispatched_1"
        assert body["tasks"][0]["server_id"] == sorted_ids[0]

        # skipped содержит failed-сервер + not_attempted.
        skipped_by_reason = {s["reason"]: s["server_id"] for s in body["skipped"]}
        assert "worker_unreachable" in skipped_by_reason
        assert "not_attempted" in skipped_by_reason
        assert skipped_by_reason["worker_unreachable"] == sorted_ids[1]
        assert skipped_by_reason["not_attempted"] == sorted_ids[2]

    async def test_partial_failure_emits_warning_audit(
        self, client, operator_token_a, make_server, make_account,
        captured_emits, monkeypatch,
    ):
        from src.core.exceptions import ServiceUnavailableError

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        srv3 = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv1.id, srv2.id, srv3.id], login="ops",
        )
        sorted_ids = sorted([srv1.id, srv2.id, srv3.id])

        call_count = {"n": 0}

        async def dispatch_then_boom(*, target_server_id, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ("tsk_dispatched_a", False)
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE", message="boom",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            dispatch_then_boom,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202

        # WARNING-event `mass_rotation.partial_failure` эмитен.
        partials = [
            e for e in captured_emits
            if e.get("action") == "mass_rotation.partial_failure"
        ]
        assert len(partials) == 1, captured_emits
        ev = partials[0]
        assert ev["status"] == "warning"
        d = ev["details"]
        assert d["task_kind"] == "account.rotate_password"
        assert d["dispatched_count"] == 1
        assert d["failed_count"] == 1
        assert d["not_attempted_count"] == 1
        assert d["dispatched_task_ids"] == ["tsk_dispatched_a"]
        assert d["failed_server_id"] == sorted_ids[1]
        assert d["not_attempted_server_ids"] == [sorted_ids[2]]
        assert d["login"] == "ops"
        assert d["department_id"] == "dep_a"

    async def test_full_success_marks_partial_failure_false(
        self, client, operator_token_a, make_server, make_account,
        captured_emits, monkeypatch,
    ):
        """Mass-rotate без сбоев — `partial_failure=False`, `next_action=None`.

        Поле явно проставлено для UX-консистентности, чтобы UI не приходилось
        defensively проверять отсутствие ключа.
        """
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv1.id, srv2.id], login="ops",
        )

        call_count = {"n": 0}

        async def ok_dispatch(**kwargs):
            call_count["n"] += 1
            return (f"tsk_ok_{call_count['n']}", False)

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
            ok_dispatch,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["partial_failure"] is False
        assert body["next_action"] is None
        assert body["status"] == "queued"
        assert len(body["tasks"]) == 2

    async def test_single_mode_unreachable_still_503(
        self, client, operator_token_a, make_server, make_account,
        captured_emits, monkeypatch,
    ):
        """Точечная ротация (mode=single) на worker_unreachable остаётся 503 —
        partial-response введён только для mass-режима с хотя бы одним
        успешным dispatch'ем."""
        from src.core.exceptions import ServiceUnavailableError

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")

        async def boom(**kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE", message="dead",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate?server_id={srv.id}",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503

        partials = [
            e for e in captured_emits
            if e.get("action") == "mass_rotation.partial_failure"
        ]
        # В single-режиме mass_rotation.partial_failure НЕ эмитим.
        assert partials == []

    async def test_mass_first_dispatch_fails_still_503(
        self, client, operator_token_a, make_server, make_account,
        captured_emits, monkeypatch,
    ):
        """Mass-режим, но сбой случился на ПЕРВОМ же сервере (нет успешных
        dispatch'ей) — ничего отменять нечего, отбиваем 503 как раньше."""
        from src.core.exceptions import ServiceUnavailableError

        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv1.id, srv2.id], login="ops",
        )

        async def boom_immediately(**kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE", message="dead-from-start",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
            boom_immediately,
        )

        resp = await client.post(
            f"{BASE}/server-accounts/{acc.id}/rotate",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503

        partials = [
            e for e in captured_emits
            if e.get("action") == "mass_rotation.partial_failure"
        ]
        # tasks пуст → партиал не эмитится.
        assert partials == []
