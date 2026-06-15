"""Regression для аудит-эмита `server_not_found` в `fetch_account_password`
и `rotate_account_password`.

Проверяем, что при отсутствующем сервере событие уходит под
`target_type="server"`, account_id уезжает в `details`, и порядок
проверок не сливает caller'у факт принадлежности account_id чужому
департаменту: server_repo.get_by_id зовётся ДО dept-check'а, поэтому
несуществующий server_id никогда не порождает фейковый
`actor_department_mismatch`.

Связанные тесты: `test_server_not_found_target_type.py::TestServerNotFoundTargetType`
закрывает базовый случай; этот файл добивает cross-dept + soft-mode
комбинации, чтобы убедиться, что dept-mismatch / dept-header-missing
не маскируют server-level miss.
"""
from __future__ import annotations

import pytest

from src.core.exceptions import NotFoundError
from src.schemas.identity import IdentityContext
from src.services import internal_service

from tests._helpers import make_emit_capture


def _identity(
    department_id: str | None = "dep_a",
    subject_type: str = "bot",
) -> IdentityContext:
    return IdentityContext(
        user_id="bot_w",
        username="tester",
        department_id=department_id,
        allowed_services=["server_service"],
        service_roles={"server_service": ["worker_bot"]},
        is_banned=False,
        platform_role=None,
        subject_type=subject_type,
    )


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(monkeypatch)


def _settings(monkeypatch, *, strict: bool = False) -> None:
    class _S:
        internal_require_dept_header = strict
        ipmi_verify_max_age_seconds = 60
        rotated_at_skew_seconds = 600
        verify_future_skew_seconds = 60

    monkeypatch.setattr(internal_service, "get_settings", lambda: _S())


def _stub_permissions_ok(monkeypatch) -> None:
    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(internal_service.permissions, "require_action", _ok)


def _stub_server_missing(monkeypatch) -> None:
    async def get_by_id(_db, _sid):
        return None

    monkeypatch.setattr(internal_service.server_repo, "get_by_id", get_by_id)


def _stub_account_missing(monkeypatch) -> None:
    async def get_by_id(_db, _aid):
        return None

    async def is_linked(_db, _aid, _sid):
        return False

    monkeypatch.setattr(internal_service.account_repo, "get_by_id", get_by_id)
    monkeypatch.setattr(internal_service.account_repo, "is_linked", is_linked)


class TestFetchAccountPasswordServerNotFound:
    @pytest.mark.asyncio
    async def test_target_type_is_server_with_account_id_in_details(
        self, monkeypatch, captured_emits, db,
    ):
        _settings(monkeypatch, strict=False)
        _stub_permissions_ok(monkeypatch)
        _stub_server_missing(monkeypatch)
        _stub_account_missing(monkeypatch)

        with pytest.raises(NotFoundError) as exc:
            await internal_service.fetch_account_password(
                db, _identity(), server_id="srv_ghost", account_id="acc_x",
                target_department_id="dep_a",
            )
        assert exc.value.error_code == "ACCOUNT_NOT_FOUND"

        failures = [
            e for e in captured_emits
            if e["action"] == "server_account.view_password"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == "srv_ghost"
        assert ev["target_type"] == "server"
        assert ev["details"]["reason"] == "server_not_found"
        assert ev["details"]["account_id"] == "acc_x"

    @pytest.mark.asyncio
    async def test_cross_dept_caller_no_fake_actor_mismatch_emitted(
        self, monkeypatch, captured_emits, db,
    ):
        # Caller из dep_b ходит за server_id, которого нет вовсе. До фикса
        # dept-check мог сработать первым на разнице header'а и actor'а;
        # сейчас server_repo.get_by_id зовётся РАНЬШЕ — иначе SIEM ловил бы
        # `actor_department_mismatch` на любых обращениях к несуществующим
        # серверам.
        _settings(monkeypatch, strict=False)
        _stub_permissions_ok(monkeypatch)
        _stub_server_missing(monkeypatch)
        _stub_account_missing(monkeypatch)

        with pytest.raises(NotFoundError):
            await internal_service.fetch_account_password(
                db,
                _identity(department_id="dep_b"),
                server_id="srv_ghost",
                account_id="acc_x",
                target_department_id="dep_a",
            )

        mismatch = [
            e for e in captured_emits
            if e["details"].get("reason") == "actor_department_mismatch"
        ]
        assert mismatch == []
        server_not_found = [
            e for e in captured_emits
            if e["details"].get("reason") == "server_not_found"
        ]
        assert len(server_not_found) == 1
        assert server_not_found[0]["target_type"] == "server"


class TestRotateAccountPasswordServerNotFound:
    @pytest.mark.asyncio
    async def test_target_type_is_server_with_account_id_in_details(
        self, monkeypatch, captured_emits, db,
    ):
        _settings(monkeypatch, strict=False)
        _stub_permissions_ok(monkeypatch)
        _stub_server_missing(monkeypatch)
        _stub_account_missing(monkeypatch)

        with pytest.raises(NotFoundError) as exc:
            await internal_service.rotate_account_password(
                db, _identity(), server_id="srv_ghost", account_id="acc_x",
                new_password="N3wPa$$", target_department_id="dep_a",
            )
        assert exc.value.error_code == "ACCOUNT_NOT_FOUND"

        failures = [
            e for e in captured_emits
            if e["action"] == "server_account.rotate_password"
            and e.get("status") == "failure"
        ]
        assert len(failures) == 1
        ev = failures[0]
        assert ev["target_id"] == "srv_ghost"
        assert ev["target_type"] == "server"
        assert ev["details"]["reason"] == "server_not_found"
        assert ev["details"]["account_id"] == "acc_x"

    @pytest.mark.asyncio
    async def test_cross_dept_caller_no_fake_actor_mismatch_emitted(
        self, monkeypatch, captured_emits, db,
    ):
        _settings(monkeypatch, strict=False)
        _stub_permissions_ok(monkeypatch)
        _stub_server_missing(monkeypatch)
        _stub_account_missing(monkeypatch)

        with pytest.raises(NotFoundError):
            await internal_service.rotate_account_password(
                db,
                _identity(department_id="dep_b"),
                server_id="srv_ghost",
                account_id="acc_x",
                new_password="N3wPa$$",
                target_department_id="dep_a",
            )

        mismatch = [
            e for e in captured_emits
            if e["details"].get("reason") == "actor_department_mismatch"
        ]
        assert mismatch == []
        server_not_found = [
            e for e in captured_emits
            if e["details"].get("reason") == "server_not_found"
        ]
        assert len(server_not_found) == 1
        assert server_not_found[0]["target_type"] == "server"
