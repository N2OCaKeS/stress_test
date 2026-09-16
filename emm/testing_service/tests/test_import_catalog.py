"""Тесты `scripts/import_catalog.py` — офлайн-инструмент импорта каталога (§13 плана).

Гоняются напрямую против сервисного слоя/БД, без HTTP — как и сам скрипт.
`stands` резолвит `department_id` живым pass-through вызовом к server_service,
поэтому мокается тем же MockTransport-приёмом, что `test_test_stands_crud.py`.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from scripts.import_catalog import _resolve_command_slots, run
from src.db.session import AsyncSessionLocal
from src.repositories import test_command_arg as command_arg_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as test_stand_repo
from src.services import server_client


def _write(tmp_path, data: dict, name: str = "catalog.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _code() -> str:
    return f"import.test.{uuid.uuid4().hex[:8]}"


# ── variable_code → variable_id ──────────────────────────────────────────────

class TestResolveCommandSlots:
    async def test_resolves_known_variable_code(self):
        async with AsyncSessionLocal() as db:
            slots = await _resolve_command_slots(db, [
                {"kind": "literal", "literal_value": "/home/u/bin/x.sh"},
                {"kind": "variable", "variable_code": "RC"},
            ])
        assert slots[0].kind == "literal"
        assert slots[0].literal_value == "/home/u/bin/x.sh"
        assert slots[1].kind == "variable"
        assert slots[1].variable_id is not None

    async def test_unknown_variable_code_raises(self):
        async with AsyncSessionLocal() as db:
            with pytest.raises(ValueError, match="unknown_var_xyz"):
                await _resolve_command_slots(db, [
                    {"kind": "variable", "variable_code": "unknown_var_xyz"},
                ])

    async def test_variable_kind_without_code_raises(self):
        async with AsyncSessionLocal() as db:
            with pytest.raises(ValueError):
                await _resolve_command_slots(db, [{"kind": "variable"}])

    async def test_unknown_kind_raises(self):
        async with AsyncSessionLocal() as db:
            with pytest.raises(ValueError):
                await _resolve_command_slots(db, [{"kind": "bogus"}])


# ── tests[] ──────────────────────────────────────────────────────────────────

class TestImportTests:
    async def test_creates_definition_and_slots(self, tmp_path):
        code = _code()
        path = _write(tmp_path, {
            "tests": [{
                "code": code,
                "full_name": "Импорт — простой тест",
                "category": "demo",
                "command": [
                    {"kind": "literal", "literal_value": "/home/u/bin/run.sh"},
                    {"kind": "variable", "variable_code": "KERNEL"},
                    {"kind": "variable", "variable_code": "TEST_USER", "override_value": "custom"},
                ],
            }],
        })

        exit_code = await run(path, bearer_token=None, dry_run=False)
        assert exit_code == 0

        async with AsyncSessionLocal() as db:
            obj = await test_definition_repo.get_by_code(db, code)
            assert obj is not None
            assert obj.full_name == "Импорт — простой тест"
            slots = await command_arg_repo.list_by_test(db, obj.id)
            assert [s.position for s in slots] == [0, 1, 2]
            assert slots[0].kind == "literal"
            assert slots[2].override_value == "custom"

    async def test_mode_field_wired_and_defaults_to_orel(self, tmp_path):
        with_mode, without_mode = _code(), _code()
        path = _write(tmp_path, {
            "tests": [
                {"code": with_mode, "full_name": "Смоленск-тест", "mode": "smolensk"},
                {"code": without_mode, "full_name": "Обычный тест"},
            ],
        })

        exit_code = await run(path, bearer_token=None, dry_run=False)
        assert exit_code == 0

        async with AsyncSessionLocal() as db:
            assert (await test_definition_repo.get_by_code(db, with_mode)).mode == "smolensk"
            assert (await test_definition_repo.get_by_code(db, without_mode)).mode == "orel"

    async def test_rerun_is_idempotent(self, tmp_path):
        code = _code()
        path = _write(tmp_path, {
            "tests": [{
                "code": code,
                "full_name": "Импорт — идемпотентность",
                "command": [{"kind": "literal", "literal_value": "/home/u/bin/run.sh"}],
            }],
        })

        first = await run(path, bearer_token=None, dry_run=False)
        second = await run(path, bearer_token=None, dry_run=False)
        assert first == 0
        assert second == 0

        async with AsyncSessionLocal() as db:
            obj = await test_definition_repo.get_by_code(db, code)
            assert obj is not None
            slots = await command_arg_repo.list_by_test(db, obj.id)
            # Повторный запуск не добавил вторую копию слота.
            assert len(slots) == 1

    async def test_bad_entry_does_not_block_others(self, tmp_path):
        good_code = _code()
        bad_code = _code()
        path = _write(tmp_path, {
            "tests": [
                {
                    "code": bad_code,
                    "full_name": "Импорт — плохая ссылка на переменную",
                    "command": [{"kind": "variable", "variable_code": "does_not_exist"}],
                },
                {
                    "code": good_code,
                    "full_name": "Импорт — валидный тест после плохого",
                    "command": [{"kind": "literal", "literal_value": "ok"}],
                },
            ],
        })

        exit_code = await run(path, bearer_token=None, dry_run=False)
        assert exit_code == 1  # есть провал — сигнализируем ненулевым кодом

        async with AsyncSessionLocal() as db:
            assert await test_definition_repo.get_by_code(db, bad_code) is None
            good = await test_definition_repo.get_by_code(db, good_code)
            assert good is not None

    async def test_dry_run_writes_nothing(self, tmp_path):
        code = _code()
        path = _write(tmp_path, {
            "tests": [{
                "code": code,
                "full_name": "Импорт — dry-run",
                "command": [{"kind": "literal", "literal_value": "ok"}],
            }],
        })

        exit_code = await run(path, bearer_token=None, dry_run=True)
        assert exit_code == 0

        async with AsyncSessionLocal() as db:
            assert await test_definition_repo.get_by_code(db, code) is None

    async def test_missing_code_fails_without_crashing(self, tmp_path):
        path = _write(tmp_path, {"tests": [{"full_name": "Без кода"}]})
        exit_code = await run(path, bearer_token=None, dry_run=False)
        assert exit_code == 1


# ── stands[] ─────────────────────────────────────────────────────────────────

class _StubServerServiceSettings:
    server_service_url = "http://server-service"
    server_service_api_key = "dbos_bot_test"
    server_request_timeout_seconds = 2.0


@pytest.fixture
def mock_server_service(monkeypatch):
    """Тот же приём подмены транспорта, что `test_test_stands_crud.py`."""
    monkeypatch.setattr(server_client, "get_settings", lambda: _StubServerServiceSettings())

    def _install(handler):
        def _build(timeout: float) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        monkeypatch.setattr(server_client, "build_client", _build)

    return _install


def _server_found(department_id: str = "dep_import_test"):
    def handler(request: httpx.Request) -> httpx.Response:
        server_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={
            "id": server_id, "hostname": f"host-{server_id}", "department_id": department_id,
        })
    return handler


class TestImportStands:
    async def test_without_bearer_token_is_reported_as_failed(self, tmp_path):
        server_id = f"srv_{uuid.uuid4().hex[:8]}"
        path = _write(tmp_path, {"stands": [{"server_id": server_id}]})

        exit_code = await run(path, bearer_token=None, dry_run=False)
        assert exit_code == 1

        async with AsyncSessionLocal() as db:
            assert await test_stand_repo.get_by_server_id(db, server_id) is None

    async def test_creates_stand_with_mocked_server_service(self, tmp_path, mock_server_service):
        server_id = f"srv_{uuid.uuid4().hex[:8]}"
        mock_server_service(_server_found(department_id="dep_import_test"))
        path = _write(tmp_path, {"stands": [{"server_id": server_id, "queue_enabled": False}]})

        exit_code = await run(path, bearer_token="dbos_pat_fake_admin_token", dry_run=False)
        assert exit_code == 0

        async with AsyncSessionLocal() as db:
            obj = await test_stand_repo.get_by_server_id(db, server_id)
            assert obj is not None
            assert obj.department_id == "dep_import_test"
            assert obj.queue_enabled is False

    async def test_rerun_skips_existing_stand(self, tmp_path, mock_server_service):
        server_id = f"srv_{uuid.uuid4().hex[:8]}"
        mock_server_service(_server_found())
        path = _write(tmp_path, {"stands": [{"server_id": server_id}]})

        first = await run(path, bearer_token="dbos_pat_fake_admin_token", dry_run=False)
        mock_server_service(_server_found())
        second = await run(path, bearer_token="dbos_pat_fake_admin_token", dry_run=False)
        assert first == 0
        assert second == 0

        async with AsyncSessionLocal() as db:
            obj = await test_stand_repo.get_by_server_id(db, server_id)
            assert obj is not None


class TestOverrideStandServerId:
    """Dev-сид пересоздаёт server_id заново на каждый прогон (`scripts/seed_dev.py`)
    — `--override-stand-server-id` подменяет захардкоженный в yaml/json id
    перед импортом, а не полагается на то, что caller сам его подставит."""

    async def test_overrides_single_stand(self, tmp_path, mock_server_service):
        real_server_id = f"srv_{uuid.uuid4().hex[:8]}"
        mock_server_service(_server_found())
        path = _write(tmp_path, {"stands": [{"server_id": "srv_placeholder"}]})

        exit_code = await run(
            path, bearer_token="dbos_pat_fake_admin_token", dry_run=False,
            override_stand_server_id=real_server_id,
        )
        assert exit_code == 0

        async with AsyncSessionLocal() as db:
            assert await test_stand_repo.get_by_server_id(db, "srv_placeholder") is None
            assert await test_stand_repo.get_by_server_id(db, real_server_id) is not None

    async def test_multiple_stands_reject_override(self, tmp_path):
        path = _write(tmp_path, {"stands": [{"server_id": "srv_a"}, {"server_id": "srv_b"}]})

        with pytest.raises(SystemExit):
            await run(
                path, bearer_token="dbos_pat_fake_admin_token", dry_run=False,
                override_stand_server_id="srv_real",
            )
