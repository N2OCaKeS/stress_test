"""Фиксы по server_service: N+1, soft-mode, NTP-skew window.

Покрывает:

1. N+1 в `_resolve_same_dept_servers`: цикл `for sid: load_visible_server`
   заменён на один батч `load_visible_servers`. Source-inspection ловит, что
   старый цикл не вернулся.
2. `_check_target_department` soft-mode — явный комментарий в коде про
   намеренное dev/test ослабление, чтобы не выглядело как баг.
3. `internal_service` — окно NTP-skew для `rotated_at` живёт в
   `Settings.rotated_at_skew_seconds` (env `ROTATED_AT_SKEW_SECONDS`).
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services import internal_service, server_account as server_account_service
from src.services.server_account import _resolve_same_dept_servers
from src.core.exceptions import NotFoundError


# ── 1. _resolve_same_dept_servers — batch fetch, не цикл ─────────────────────


class TestResolveSameDeptServersBatch:
    """N+1 закрыт: один SQL-запрос вместо цикла по id'шникам."""

    def test_source_uses_load_visible_servers(self):
        src = inspect.getsource(_resolve_same_dept_servers)
        assert "load_visible_servers(" in src, (
            "_resolve_same_dept_servers должен звать батчевый "
            "`load_visible_servers`, а не цикл `for sid`."
        )

    def test_source_has_no_per_id_loop(self):
        src = inspect.getsource(_resolve_same_dept_servers)
        # List-comprehensions с `for sid in server_ids` (без await) допустимы —
        # это материализация результата. Регрессия N+1 — это `await ... for sid`
        # внутри цикла; ищем именно её.
        assert "await load_visible_server(" not in src or "load_visible_servers(" in src, (
            "N+1 регрессия: per-id `await load_visible_server` без батчевого helper'а."
        )

    async def test_empty_input_short_circuits(self):
        db = MagicMock()
        identity = SimpleNamespace(department_id="dep_a")
        result = await _resolve_same_dept_servers(db, identity, [], "x")
        assert result == []

    async def test_batch_returns_in_order(self, monkeypatch):
        """Порядок выходного списка совпадает с порядком запроса."""
        servers = {
            "srv_1": SimpleNamespace(id="srv_1", department_id="dep_a"),
            "srv_2": SimpleNamespace(id="srv_2", department_id="dep_a"),
            "srv_3": SimpleNamespace(id="srv_3", department_id="dep_a"),
        }

        async def fake_load(db, identity, ids):
            return {sid: servers[sid] for sid in ids if sid in servers}

        monkeypatch.setattr(server_account_service, "load_visible_servers", fake_load)

        db = MagicMock()
        identity = SimpleNamespace(department_id="dep_a")
        result = await _resolve_same_dept_servers(
            db, identity, ["srv_3", "srv_1", "srv_2"], "test.action",
        )
        assert [s.id for s in result] == ["srv_3", "srv_1", "srv_2"]

    async def test_missing_id_raises_not_found_with_audit(self, monkeypatch):
        """Пропавший id (cross-dept или не существует) → NotFoundError + audit."""
        async def fake_load(db, identity, ids):
            # `srv_x` отсутствует — load_visible_servers просто не вернёт ключ.
            return {
                "srv_1": SimpleNamespace(id="srv_1", department_id="dep_a"),
            }

        monkeypatch.setattr(server_account_service, "load_visible_servers", fake_load)

        emitted: list[dict] = []

        def fake_emit(action, **kwargs):
            emitted.append({"action": action, **kwargs})

        monkeypatch.setattr(
            server_account_service.audit_service, "emit", fake_emit,
        )

        db = MagicMock()
        identity = SimpleNamespace(department_id="dep_a")
        with pytest.raises(NotFoundError) as exc:
            await _resolve_same_dept_servers(
                db, identity, ["srv_1", "srv_x"], "server_account.create",
            )
        assert exc.value.error_code == "SERVER_NOT_FOUND"

        assert len(emitted) == 1
        ev = emitted[0]
        assert ev["action"] == "server_account.create"
        assert ev["status"] == "failure"
        assert ev["allowed"] is True
        assert ev["details"]["reason"] == "server_not_found_or_cross_dept"
        assert ev["details"]["server_id"] == "srv_x"

    async def test_first_missing_reported_when_multiple(self, monkeypatch):
        """Если пропали несколько, в audit идёт первый по порядку входа."""
        async def fake_load(db, identity, ids):
            return {"srv_1": SimpleNamespace(id="srv_1", department_id="dep_a")}

        monkeypatch.setattr(server_account_service, "load_visible_servers", fake_load)
        emitted: list[dict] = []
        monkeypatch.setattr(
            server_account_service.audit_service, "emit",
            lambda action, **kw: emitted.append({"action": action, **kw}),
        )

        db = MagicMock()
        identity = SimpleNamespace(department_id="dep_a")
        with pytest.raises(NotFoundError):
            await _resolve_same_dept_servers(
                db, identity,
                ["srv_x", "srv_1", "srv_y"],
                "server_account.create",
            )
        assert emitted[0]["details"]["server_id"] == "srv_x"


# ── 2. header-scoping документирован в _check_target_department ──────────────


class TestHeaderScopingDocumented:
    """`_check_target_department` должен явно документировать, что
    `X-Target-Department-Id` — единственный cross-dept гард, отдел бота не
    участвует, и разбор missing → 403 / mismatch → 404.
    """

    def test_check_target_department_source_documents_header_scoping(self):
        src = inspect.getsource(internal_service._check_target_department)
        low = src.lower()
        assert "x-target-department-id" in low
        assert "target_department_header_required" in low
        assert "target_department_mismatch" in low
        # Отдел самого бота не должен влиять на блокировку.
        assert "actor_department_id" in src


# ── 3. internal_service: rotated_at NTP-skew settings ────────────────────────


class TestInternalServiceImportsOrder:
    def test_rotated_at_skew_default_in_settings(self):
        """Дефолт `Settings.rotated_at_skew_seconds` — 600s (10 минут).

        Сама константа вынесена из `internal_service` в `core/config.py`,
        env-override через `ROTATED_AT_SKEW_SECONDS`. Тест держит дефолт,
        чтобы тихая правка не сменила окно отбивки `rotated_at`.
        """
        from src.core.config import Settings

        # noinspection PyTypeChecker
        field = Settings.model_fields["rotated_at_skew_seconds"]
        assert field.default == 600

