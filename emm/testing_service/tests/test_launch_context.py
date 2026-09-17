"""Вычисляемая часть `launch_context` и полнота данных импортированного каталога.

Главный тест здесь — `TestAlltaCatalogResolves`: он импортирует НАСТОЯЩИЙ
`scripts/import_catalog.allta.yaml` и резолвит команду каждого из 61 теста.
Синтетический слот такую дыру не ловит: до этого все тесты очереди собирали
`launch_context` руками под свой единственный слот, а в реальном каталоге
переменных впятеро больше и половине из них неоткуда было взять значение.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
import yaml

from scripts.import_catalog import run as import_run
from src.core.exceptions import AppException
from src.db.session import AsyncSessionLocal
from src.models import TestDefinition, TestStand
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as test_stand_repo
from src.services import launch_context as lc
from src.services import server_client
from src.services.test_command_arg import resolve_dates_content

ALLTA_CATALOG = Path(__file__).resolve().parents[1] / "scripts" / "import_catalog.allta.yaml"

LAUNCH_CTX = {"RC": "1.8.5.46", "KERNEL": "6.1.0", "MODE": "orel"}


def _stand(legacy_token: str | None = "stand3") -> TestStand:
    return TestStand(
        id="stand_" + uuid.uuid4().hex, server_id="srv_x", department_id="dep_a",
        legacy_token=legacy_token, queue_enabled=True, is_active=True,
    )


def _test(category: str | None = "postgresql", full_name: str = "postgresql benchmark") -> TestDefinition:
    return TestDefinition(
        id="tdef_x", code="postgresql.base", full_name=full_name, category=category,
        readiness="ready", mode="orel",
    )


# ── чистые вычисления ────────────────────────────────────────────────────────

class TestComputedValues:
    def test_matches_legacy_formulas(self):
        values = lc.computed_values(_test(), _stand("stand3"), LAUNCH_CTX)

        # allta_back.py:175
        assert values["TEST_CYCLE_NAME"] == "1.8.5.46_orel_6.1.0_stand3"
        # allta_back.py:494 — имя тест-кейса Zephyr
        assert values["TEST_CASE_NAME"] == "postgresql benchmark"
        # allta_image_conf.py:122-127
        assert values["PARENT_PAGE"] == "STRESS_report 1.8.5.46 ⬝ PostgreSQL"
        # backup_image.py:297
        assert values["CONFLUENCE_NEW_PAGE"] == "postgresql benchmark_1.8.5.46_orel_6.1.0_stand3"
        # allta_back.py:169 — голый номер, целевые скрипты требуют choices=['1','3',…]
        assert values["STAND"] == "3"

    def test_stand_number_needs_legacy_token(self):
        values = lc.computed_values(_test(), _stand(None), LAUNCH_CTX)
        assert "STAND" not in values

    def test_falls_back_to_internal_id_when_stand_has_no_alias(self):
        stand = _stand(None)
        values = lc.computed_values(_test(), stand, LAUNCH_CTX)
        assert values["TEST_CYCLE_NAME"].endswith(stand.id)

    def test_unknown_category_yields_no_parent_page(self):
        values = lc.computed_values(_test(category="brand_new"), _stand(), LAUNCH_CTX)
        assert "PARENT_PAGE" not in values

    def test_topics_cover_every_category_of_the_allta_catalog(self):
        """Рубрика Confluence должна найтись для каждой ветки реального каталога.

        Иначе `-cpp` снова окажется без значения — молча, на первом же запуске
        теста из непокрытой категории.
        """
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        categories = {t.get("category") for t in data["tests"] if t.get("category")}
        missing = sorted(c for c in categories if c not in lc._TOPIC_BY_CATEGORY)
        assert missing == []

    def test_computed_value_wins_over_caller_supplied_one(self):
        ctx = {**LAUNCH_CTX, "TEST_CASE_NAME": "подделка"}
        values = lc.computed_values(_test(), _stand(), ctx)
        assert values["TEST_CASE_NAME"] == "postgresql benchmark"


class TestStandToken:
    def test_prefers_alias(self):
        assert lc.stand_token(_stand("stand14")) == "stand14"

    def test_number_from_alias(self):
        assert lc.stand_number(_stand("stand14")) == "14"

    def test_number_is_none_without_alias(self):
        assert lc.stand_number(_stand(None)) is None


# ── реальный каталог ─────────────────────────────────────────────────────────

class _StubServerServiceSettings:
    server_service_url = "http://server-service"
    server_service_api_key = "dbos_bot_test"
    server_request_timeout_seconds = 2.0


@pytest.fixture
def mock_server_service(monkeypatch):
    monkeypatch.setattr(server_client, "get_settings", lambda: _StubServerServiceSettings())

    def handler(request: httpx.Request) -> httpx.Response:
        server_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={
            "id": server_id, "hostname": f"host-{server_id}", "department_id": "dep_a",
        })

    def _build(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(server_client, "build_client", _build)


@pytest.fixture
async def imported_allta_catalog(mock_server_service):
    """Настоящий каталог allta_app в БД + единственный стенд с именем `stand3`."""
    server_id = f"srv_{uuid.uuid4().hex[:10]}"
    exit_code = await import_run(
        ALLTA_CATALOG, bearer_token="dbos_pat_fake_admin_token", dry_run=False,
        override_stand_server_id=server_id, stand_legacy_token="stand3",
    )
    assert exit_code == 0
    async with AsyncSessionLocal() as db:
        stand = await test_stand_repo.get_by_server_id(db, server_id)
    assert stand is not None
    return stand


class TestAlltaCatalogResolves:
    async def test_every_imported_test_resolves_without_missing_variables(
        self, imported_allta_catalog,
    ):
        """Регрессия на P0 «`launch_context` не несёт половины переменных».

        До правки любой из 61 теста падал в `claim_next` с
        `LAUNCH_CONTEXT_VARIABLE_MISSING` ещё до SSH.
        """
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        codes = [t["code"] for t in data["tests"]]
        assert len(codes) == 61

        failures: list[str] = []
        async with AsyncSessionLocal() as db:
            for code in codes:
                test = await test_definition_repo.get_by_code(db, code)
                assert test is not None, code
                ctx = dict(LAUNCH_CTX)
                ctx.update(lc.computed_values(test, imported_allta_catalog, ctx))
                try:
                    content = await resolve_dates_content(db, test.id, ctx)
                except AppException as exc:
                    failures.append(f"{code}: {exc.error_code} {exc.details}")
                    continue
                assert content, code

        assert failures == []

    async def test_folder_tree_id_is_a_static_placeholder(self, imported_allta_catalog):
        """`-fti` больше не переменная без источника — у слота свой override."""
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, "postgresql.base")
            ctx = dict(LAUNCH_CTX)
            ctx.update(lc.computed_values(test, imported_allta_catalog, ctx))
            content = await resolve_dates_content(db, test.id, ctx)
        assert "-fti none" in content

    async def test_stand_number_is_bare_digits_in_the_command(self, imported_allta_catalog):
        """`-sn` целевые скрипты объявляют через `choices=['1','3','4',…]`."""
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, "file_systems.xfs")
            ctx = dict(LAUNCH_CTX)
            ctx.update(lc.computed_values(test, imported_allta_catalog, ctx))
            content = await resolve_dates_content(db, test.id, ctx)
        assert "-sn 3" in content

    async def test_legacy_per_test_timeouts_are_imported(self, imported_allta_catalog):
        """Легаси `allta_back.py:118-121` — `TEST_TIMEOUTS = {'syslog-ng-cwl': 36,
        'auditd-u': 20}` часов, дефолт 12ч. Без явного `timeout_seconds` оба
        теста убивались бы на 12-м часу и засчитывались как провал."""
        async with AsyncSessionLocal() as db:
            useraud = await test_definition_repo.get_by_code(db, "auditd.useraud")
            cwl = await test_definition_repo.get_by_code(db, "syslog_ng.check_write_log")
        assert useraud.timeout_seconds == 20 * 3600
        assert cwl.timeout_seconds == 36 * 3600

    async def test_stand3_tests_are_pinned_and_others_report_missing_stand(
        self, imported_allta_catalog,
    ):
        """Привязки перенесены из легаси `stands_groups`.

        В этой установке есть только `stand3`, поэтому пинится ровно его
        группа; остальные легаси-стенды в dev не заведены — их тесты остаются
        без привязки осознанно, импортёр перечисляет их отдельно.
        """
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        expected_stand3 = {t["code"] for t in data["tests"] if t.get("pinned_stand_token") == "stand3"}
        assert len(expected_stand3) == 13

        async with AsyncSessionLocal() as db:
            for t in data["tests"]:
                test = await test_definition_repo.get_by_code(db, t["code"])
                if t["code"] in expected_stand3:
                    assert test.pinned_stand_id == imported_allta_catalog.id, t["code"]
                else:
                    assert test.pinned_stand_id is None, t["code"]

    async def test_catalog_carries_legacy_pinning_for_57_of_61(self):
        """Сами данные привязки: 57 тестов из 61 имеют легаси-стенд.

        Четыре исключения — tantor vanilla/kernels и оба overflow: их нет в
        `stands_groups` allta_app, то есть привязки не было и в легаси.
        """
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        with_token = [t["code"] for t in data["tests"] if t.get("pinned_stand_token")]
        without = sorted(t["code"] for t in data["tests"] if not t.get("pinned_stand_token"))
        assert len(with_token) == 57
        assert without == [
            "overflow.ram", "overflow.storage_drive",
            "postgresql.tantor_kernels", "postgresql.tantor_vanilla",
        ]
