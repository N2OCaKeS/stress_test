"""Легаси-формулы запуска как переменные каталога и полнота импортированного каталога.

До значения `CONFLUENCE_NEW_PAGE`/`TEST_CYCLE_NAME`/`TEST_CASE_NAME`/
`PARENT_PAGE`/`STAND` считал код (`launch_context.computed_values` со словарём
рубрик по ветке). Теперь это сид-переменные с источниками `template`/
`test_field`/`stand` (миграция `tp01_seed_legacy_formulas`), и тесты ниже
сверяют резолвер с прежними формулами — `_legacy_values` здесь и есть эталон
(копия удалённого кода, а не его импорт).

Главный тест — `TestAlltaCatalogResolves`: он импортирует НАСТОЯЩИЙ
`scripts/import_catalog.allta.yaml` и резолвит команду каждого из 62 тестов.
После учётные данные в командах каталога — из интеграций отдела стенда
(фикстура `integration_for_catalog`), а `FOLDER_TREE_ID` — из записи
`zephyr_folders` отдела стенда и версии ОС (, фикстура
`zephyr_folder_for_catalog`).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
import yaml

from scripts.import_catalog import run as import_run
from src.core.exceptions import AppException, DomainValidationError, NotFoundError
from src.db.session import AsyncSessionLocal
from src.models import TestDefinition, TestStand
from src.repositories import department_integration_settings as dis_repo
from src.repositories import test_definition as test_definition_repo
from src.repositories import test_stand as test_stand_repo
from src.repositories import zephyr_folder as zephyr_folder_repo
from src.services import secret_client, server_client
from src.services import variable_resolver as vr
from src.services.test_command_arg import resolve_dates_content
from tests.conftest import auth_hdr

ALLTA_CATALOG = Path(__file__).resolve().parents[1] / "scripts" / "import_catalog.allta.yaml"

# `RC` — id карточки версии ОС (как у `test_run.py`/`public_queue.py`), имя
# версии резолвится через `server_client.resolve_os_version_info`.
LAUNCH_CTX = {"RC": "osv_5e46b2d1", "KERNEL": "6.1.0", "MODE": "orel"}

OS_VERSIONS: dict[str, server_client.OsVersionInfo] = {
    "osv_5e46b2d1": server_client.OsVersionInfo(name="1.8.5.46", is_urgent_update=False, rc_number="RC5"),
    "osv_8a16c9e0": server_client.OsVersionInfo(name="1.8.1.6", is_urgent_update=False, rc_number="RC6"),
    "osv_uu179120": server_client.OsVersionInfo(name="1.7.9.UU.1.2", is_urgent_update=True, rc_number="RC2"),
    # Версия без записи `zephyr_folders` — для проверки ошибки резолва `-fti`.
    "osv_nofolder": server_client.OsVersionInfo(name="1.8.9.1", is_urgent_update=False, rc_number="RC1"),
}


@pytest.fixture(autouse=True)
def os_version_catalog(monkeypatch):
    """Каталог версий ОС server_service: `osv_<hex>` → карточка."""
    async def fake_resolve(os_version_id: str) -> server_client.OsVersionInfo:
        info = OS_VERSIONS.get(os_version_id)
        if info is None:
            raise NotFoundError(error_code="OS_VERSION_NOT_FOUND", message=os_version_id)
        return info

    monkeypatch.setattr(server_client, "resolve_os_version_info", fake_resolve)
# id папки Zephyr приходит из `zephyr_folders`, не из контекста запуска.
CATALOG_CTX = LAUNCH_CTX
CATALOG_FOLDER_TREE_ID = "4242"

FORMULA_CODES = ("CONFLUENCE_NEW_PAGE", "TEST_CYCLE_NAME", "TEST_CASE_NAME", "PARENT_PAGE", "STAND")

# Рубрика по git-ветке из удалённого `launch_context.py` — только эталон для
# сверки. Боевой резолв берёт рубрику из `test_definitions.changelog_component` (D8).
LEGACY_TOPIC_BY_BRANCH: dict[str, str] = {
    "postgresql": "PostgreSQL",
    "file_systems": "Файловые системы",
    "cluster_file_systems": "Файловые системы",
    "auditd": "Системные службы",
    "syslog_ng": "Системные службы",
    "overflow": "Системные службы",
    "kernel": "Системные службы",
    "astraevents": "Системные службы",
    "astra_openvpn": "Системные службы",
    "exim": "Системные службы",
    "linux_system": "UnixBench",
    "freeipa": "FreeIPA",
    "parsec": "Parsec",
    "apache2": "Apache",
    "docker": "Docker/Podman/LXC",
    "virt": "Qemu/KVM/Libvirt",
    "network": "Network",
}


def _legacy_values(test: TestDefinition, stand: TestStand, ctx: dict, *, debug: bool = False) -> dict[str, str]:
    """Формулы до (allta_back.py:169,175,494; backup_image.py:297; allta_image_conf.py:122-127)."""
    # Легаси `args.RELEASE` — имя версии (`1.8.5.46`), не id каталога.
    rc, mode, kernel = OS_VERSIONS[ctx["RC"]].name, ctx["MODE"], ctx["KERNEL"]
    token = stand.legacy_token or stand.id
    prefix = "DEBUG_" if debug else ""
    # `args.TEST` легаси — короткое имя (ключ словаря `tests`), у теста без
    # него — полное (прежнее поведение testing_service).
    short = test.short_name or test.full_name
    values = {
        "CONFLUENCE_NEW_PAGE": f"{prefix}{short}_{rc}_{mode}_{kernel}_{token}",
        "TEST_CYCLE_NAME": f"{rc}_{mode}_{kernel}_{token}",
        "TEST_CASE_NAME": test.full_name,
    }
    topic = LEGACY_TOPIC_BY_BRANCH.get(test.category or "")
    if topic:
        values["PARENT_PAGE"] = f"{prefix}STRESS_report {rc} ⬝ {topic}"
    digits = "".join(ch for ch in (stand.legacy_token or "") if ch.isdigit())
    if digits:
        values["STAND"] = digits
    return values


def _stand(legacy_token: str | None = "stand3") -> TestStand:
    return TestStand(
        id="stand_" + uuid.uuid4().hex, server_id="srv_x", department_id="dep_a",
        legacy_token=legacy_token, queue_enabled=True, is_active=True,
    )


def _test(
    category: str | None = "postgresql",
    full_name: str = "postgresql benchmark",
    changelog_component: str | None = "PostgreSQL",
    short_name: str | None = None,
) -> TestDefinition:
    return TestDefinition(
        id="tdef_x", code="postgresql.base", full_name=full_name, category=category,
        changelog_component=changelog_component, readiness="ready", mode="orel",
        short_name=short_name,
    )


async def _resolve_formulas(db, test, stand, ctx, *, debug: bool = False, codes=FORMULA_CODES) -> dict[str, str]:
    rctx = vr.ResolveContext(
        db=db, department_id=stand.department_id, test=test, stand=stand,
        launch_context=dict(ctx), debug=debug,
    )
    values = {}
    for code in codes:
        values[code] = (await vr.resolve_code(rctx, code)).value
    return values


# ── сид-формулы против легаси ────────────────────────────────────────────────

class TestSeededFormulas:
    async def test_matches_legacy_formulas(self):
        async with AsyncSessionLocal() as db:
            values = await _resolve_formulas(db, _test(), _stand("stand3"), LAUNCH_CTX)

        # allta_back.py:175
        assert values["TEST_CYCLE_NAME"] == "1.8.5.46_orel_6.1.0_stand3"
        # allta_back.py:494 — имя тест-кейса Zephyr
        assert values["TEST_CASE_NAME"] == "postgresql benchmark"
        # allta_image_conf.py:122-127
        assert values["PARENT_PAGE"] == "STRESS_report 1.8.5.46 ⬝ PostgreSQL"
        # backup_image.py:297; short_name не задан — fallback на full_name
        assert values["CONFLUENCE_NEW_PAGE"] == "postgresql benchmark_1.8.5.46_orel_6.1.0_stand3"
        # allta_back.py:169 — голый номер, целевые скрипты требуют choices=['1','3',…]
        assert values["STAND"] == "3"

    async def test_short_name_wins_over_full_name_when_set(self):
        """Колонка `short_name` — легаси-ключ `XFS`, а не полное имя (D6)."""
        test = _test(full_name="file system benchmark. XFS", short_name="XFS")
        async with AsyncSessionLocal() as db:
            values = await _resolve_formulas(
                db, test, _stand("stand3"), LAUNCH_CTX, codes=("CONFLUENCE_NEW_PAGE",),
            )
        assert values["CONFLUENCE_NEW_PAGE"] == "XFS_1.8.5.46_orel_6.1.0_stand3"

    async def test_stand_number_needs_legacy_token(self):
        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError) as exc:
                await _resolve_formulas(db, _test(), _stand(None), LAUNCH_CTX, codes=("STAND",))
        assert exc.value.error_code == "VARIABLE_VALUE_MISSING"

    async def test_falls_back_to_internal_id_when_stand_has_no_alias(self):
        stand = _stand(None)
        async with AsyncSessionLocal() as db:
            values = await _resolve_formulas(db, _test(), stand, LAUNCH_CTX, codes=("TEST_CYCLE_NAME",))
        assert values["TEST_CYCLE_NAME"].endswith(stand.id)

    async def test_test_without_topic_fails_parent_page_explicitly(self):
        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError) as exc:
                await _resolve_formulas(
                    db, _test(changelog_component=None), _stand(), LAUNCH_CTX, codes=("PARENT_PAGE",),
                )
        assert exc.value.error_code == "VARIABLE_VALUE_MISSING"
        assert exc.value.details["fields"] == ["changelog_component"]

    async def test_topic_comes_from_changelog_component_not_branch(self):
        """D8: рубрика — `changelog_component`, ветка монорепо на неё не влияет."""
        async with AsyncSessionLocal() as db:
            values = await _resolve_formulas(
                db, _test(category="brand_new", changelog_component="Network"), _stand(), LAUNCH_CTX,
                codes=("PARENT_PAGE",),
            )
        assert values["PARENT_PAGE"] == "STRESS_report 1.8.5.46 ⬝ Network"

    async def test_caller_cannot_forge_computed_value(self):
        ctx = {**LAUNCH_CTX, "TEST_CASE_NAME": "подделка", "STAND": "99"}
        async with AsyncSessionLocal() as db:
            values = await _resolve_formulas(db, _test(), _stand(), ctx, codes=("TEST_CASE_NAME", "STAND"))
        assert values == {"TEST_CASE_NAME": "postgresql benchmark", "STAND": "3"}

    async def test_debug_mode_prefixes_confluence_targets(self):
        """Owner п.11: результат debug-запуска не должен попасть на боевую
        страницу Confluence обычного прогона — отдельный `DEBUG_`-префикс у
        `CONFLUENCE_NEW_PAGE`/`PARENT_PAGE` (переменная `DEBUG_PREFIX`)."""
        stand = _stand("stand3")
        async with AsyncSessionLocal() as db:
            normal = await _resolve_formulas(db, _test(), stand, LAUNCH_CTX, debug=False)
            debug = await _resolve_formulas(db, _test(), stand, LAUNCH_CTX, debug=True)

        assert debug["CONFLUENCE_NEW_PAGE"] == "DEBUG_" + normal["CONFLUENCE_NEW_PAGE"]
        assert debug["PARENT_PAGE"] == "DEBUG_" + normal["PARENT_PAGE"]
        # Остальные переменные не завязаны на Confluence — debug их не трогает.
        for code in ("TEST_CYCLE_NAME", "TEST_CASE_NAME", "STAND"):
            assert debug[code] == normal[code]

    async def test_seeded_formulas_are_data(self, client, no_role_token):
        """Формулы лежат в каталоге и видны через API (правятся в UI)."""
        expected = {
            "TEST_CYCLE_NAME": ("template", {"template": "{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}"}),
            "CONFLUENCE_NEW_PAGE": ("template", {
                "template": "{IS_DEBUG_PREFIX}{TEST_SHORT_NAME}_{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}",
            }),
            "PARENT_PAGE": ("template", {"template": "{IS_DEBUG_PREFIX}STRESS_report {RC_NAME} ⬝ {TEST_TOPIC}"}),
            "TEST_TOPIC": ("test_field", {"field": "changelog_component"}),
            "TEST_SHORT_NAME": ("test_field", {"field": "short_name", "fallback": "full_name"}),
            "TEST_CASE_NAME": ("test_field", {"field": "full_name"}),
            "STAND": ("stand", {"field": "number"}),
            "STAND_TOKEN": ("stand", {"field": "legacy_token", "fallback": "id"}),
            "RC_NAME": ("os_version", {"field": "name", "segments": None, "uu_segments": None}),
            "RC_RELEASE": ("os_version", {"field": "name", "segments": 3, "uu_segments": 5}),
            "RC_BRANCH": ("os_version", {"field": "name", "segments": 2, "uu_segments": None}),
            "RC_NUMBER": ("os_version", {"field": "rc_number", "segments": None, "uu_segments": None}),
            "STARTER_ARGS_TEMPLATE": ("static", {
                "value": "{TEST_BRANCH} {GIT_TOKEN_FILE} {DATES_FILE} {RC_NAME} {STARTER_SUFFIX}",
            }),
            "TEST_BRANCH": ("launch_context", None),
            "GIT_TOKEN_FILE": ("launch_context", None),
            "DATES_FILE": ("launch_context", None),
            "STARTER_SUFFIX": ("launch_context", None),
            "DEBUG_PREFIX": ("static", {"value": "DEBUG_"}),
            "IS_DEBUG_PREFIX": ("template", {"template": "{DEBUG_PREFIX}", "when": "debug"}),
        }
        for code, (source, source_ref) in expected.items():
            resp = await client.get(
                f"/api/testing/v1/global-variables/by-code/{code}", headers=auth_hdr(no_role_token),
            )
            assert resp.status_code == 200, code
            assert (resp.json()["source"], resp.json()["source_ref"]) == (source, source_ref), code


class TestSeededOsVersionVariables:
    """имя версии и производные — из карточки версии, не из `RC`."""

    async def test_regular_release(self):
        ctx = {**LAUNCH_CTX, "RC": "osv_8a16c9e0"}
        async with AsyncSessionLocal() as db:
            values = await _resolve_formulas(
                db, _test(), _stand("stand3"), ctx,
                codes=("RC_NAME", "RC_RELEASE", "RC_BRANCH", "RC_NUMBER", "TEST_CYCLE_NAME"),
            )
        assert values == {
            "RC_NAME": "1.8.1.6",
            "RC_RELEASE": "1.8.1",      # allta_back.py:352-355
            "RC_BRANCH": "1.8",
            "RC_NUMBER": "RC6",
            "TEST_CYCLE_NAME": "1.8.1.6_orel_6.1.0_stand3",
        }

    async def test_urgent_update_release_keeps_five_segments(self):
        """Легаси `allta_back.py:356-359`: `x.y.z.UU.n.m` → релиз из 5 сегментов."""
        ctx = {**LAUNCH_CTX, "RC": "osv_uu179120"}
        async with AsyncSessionLocal() as db:
            values = await _resolve_formulas(
                db, _test(), _stand("stand3"), ctx, codes=("RC_NAME", "RC_RELEASE", "RC_BRANCH"),
            )
        assert values == {"RC_NAME": "1.7.9.UU.1.2", "RC_RELEASE": "1.7.9.UU.1", "RC_BRANCH": "1.7"}


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
async def integration_for_catalog(monkeypatch):
    """Интеграции отдела `dep_a` (отдел стенда из `mock_server_service`) — D2.

    `--username`/`--token`/`-ba`/`--confluence-space` каталога — переменные
    источника `department_integration`; без настроек отдела claim падает с
    `DEPARTMENT_INTEGRATION_NOT_CONFIGURED`.
    """
    creds = {"cred_jira": ("jira-bot", "jira basic auth"), "cred_conf": ("conf-bot", "conf-token")}

    async def reveal(cred_id: str) -> tuple[str, str]:
        return creds[cred_id]

    monkeypatch.setattr(secret_client, "reveal_credential", reveal)
    fields = {
        "credential_id": "cred_jira", "confluence_credential_id": "cred_conf",
        "stp_matrix_confluence_space": "DEVQA",
    }
    async with AsyncSessionLocal() as db:
        existing = await dis_repo.get_by_department(db, "dep_a")
        if existing is None:
            await dis_repo.create(db, {"id": f"dis_{uuid.uuid4().hex[:8]}", "department_id": "dep_a", **fields})
        else:
            await dis_repo.update(db, existing, fields)
        await db.commit()
    return creds


@pytest.fixture
async def zephyr_folder_for_catalog():
    """Папки Zephyr отдела `dep_a` для версий `OS_VERSIONS` (кроме `osv_nofolder`)."""
    async with AsyncSessionLocal() as db:
        for os_version_id, info in OS_VERSIONS.items():
            if os_version_id == "osv_nofolder":
                continue
            if await zephyr_folder_repo.get_by_department_and_os_version(db, "dep_a", os_version_id):
                continue
            await zephyr_folder_repo.create(db, {
                "id": f"zfold_{uuid.uuid4().hex[:8]}", "department_id": "dep_a",
                "os_version_id": os_version_id, "folder_path": f"/stress_test/{info.name}",
                "folder_tree_id": CATALOG_FOLDER_TREE_ID,
            })
        await db.commit()


@pytest.fixture
async def imported_allta_catalog(mock_server_service, integration_for_catalog, zephyr_folder_for_catalog):
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

        До правки любой из 62 тестов падал в `claim_next` с
        `LAUNCH_CONTEXT_VARIABLE_MISSING` ещё до SSH.
        """
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        codes = [t["code"] for t in data["tests"]]
        assert len(codes) == 62

        failures: list[str] = []
        async with AsyncSessionLocal() as db:
            for code in codes:
                test = await test_definition_repo.get_by_code(db, code)
                assert test is not None, code
                try:
                    content = await resolve_dates_content(
                        db, test.id, CATALOG_CTX, stand=imported_allta_catalog,
                    )
                except AppException as exc:
                    failures.append(f"{code}: {exc.error_code} {exc.details}")
                    continue
                assert content, code
                # `-tcv`/`-vbox` и заголовки — имя версии, id каталога наружу не уходит.
                assert LAUNCH_CTX["RC"] not in content, code

        assert failures == []

    async def test_tcv_and_vbox_carry_rc_name(self, imported_allta_catalog):
        """`-tcv`/`-vbox` ссылаются на `RC_NAME` (`backup_image.py:309-310`)."""
        ctx = {**CATALOG_CTX, "RC": "osv_8a16c9e0"}
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, "kernel.segfault")
            content = await resolve_dates_content(db, test.id, ctx, stand=imported_allta_catalog)
        assert "-tcv 1.8.1.6 -vbox 1.8.1.6 -testname segfault" in content

    async def test_no_catalog_slot_points_at_raw_rc(self):
        """Слоты `-tcv`/`-vbox` каталога — `RC_NAME`; `RC` (id карточки) остаётся
        только для server_service/ACS."""
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        flags: dict[str, int] = {}
        for test in data["tests"]:
            slots = test.get("command") or []
            for prev, slot in zip(slots, slots[1:]):
                assert slot.get("variable_code") != "RC", test["code"]
                if slot.get("variable_code") == "RC_NAME":
                    flags[prev.get("literal_value")] = flags.get(prev.get("literal_value"), 0) + 1
        assert flags == {"-tcv": 62, "-vbox": 21}

    @pytest.mark.parametrize("debug", [False, True])
    async def test_every_imported_test_matches_legacy_formulas(self, imported_allta_catalog, debug):
        """Критерий приёмки: для каждого теста каталога сид-переменные дают
        те же токены, что прежний `computed_values`, с одной поправкой:
        `CONFLUENCE_NEW_PAGE` строится из `short_name` (легаси `args.TEST`,
        D6), а не из полного имени."""
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        mismatches: list[str] = []
        async with AsyncSessionLocal() as db:
            for item in data["tests"]:
                test = await test_definition_repo.get_by_code(db, item["code"])
                expected = _legacy_values(test, imported_allta_catalog, LAUNCH_CTX, debug=debug)
                actual = await _resolve_formulas(
                    db, test, imported_allta_catalog, LAUNCH_CTX, debug=debug, codes=tuple(expected),
                )
                if actual != expected:
                    mismatches.append(f"{item['code']}: {actual} != {expected}")
                # Прежняя формула знала рубрику каждой ветки — новая тоже.
                assert "PARENT_PAGE" in expected, item["code"]
        assert mismatches == []

    async def test_folder_tree_id_has_no_placeholder(self, imported_allta_catalog):
        """ снял `override_value: none` у `-fti` берёт id папки из
        `zephyr_folders`; нет записи — ошибка с подсказкой, а не пустой `-fti`.
        Значение из `launch_context` источник `zephyr_folder` не читает."""
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, "postgresql.base")
            with pytest.raises(DomainValidationError) as exc:
                await resolve_dates_content(
                    db, test.id, {**LAUNCH_CTX, "RC": "osv_nofolder", "FOLDER_TREE_ID": "9999"},
                    stand=imported_allta_catalog,
                )
            content = await resolve_dates_content(db, test.id, CATALOG_CTX, stand=imported_allta_catalog)
        assert exc.value.error_code == "VARIABLE_VALUE_MISSING"
        assert exc.value.details["slot_variable"] == "FOLDER_TREE_ID"
        assert "hint" in exc.value.details
        assert "-fti 4242" in content
        assert "none" not in content.split()

    async def test_stand_number_is_bare_digits_in_the_command(self, imported_allta_catalog):
        """`-sn` целевые скрипты объявляют через `choices=['1','3','4',…]`."""
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, "file_systems.xfs")
            content = await resolve_dates_content(db, test.id, CATALOG_CTX, stand=imported_allta_catalog)
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

    async def test_legacy_matrix_labels_and_components_cover_the_catalog(
        self, imported_allta_catalog,
    ):
        """Сокращения строк СТП-матрицы (`testname_columns`) и компоненты
        changelog-фильтра (`tests_list`) — из `allta_image_conf.py`. Обе
        колонки заполнены у всех 62 тестов: пустое сокращение печатало бы в
        матрице полное имя, а пустой компонент выкидывал бы тест из
        changelog-объёма."""
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        assert all(t.get("matrix_label") for t in data["tests"])
        assert all(t.get("changelog_component") for t in data["tests"])
        # Компонент — ключ легаси `tests_list`, а не `category` (git-ветка).
        assert {t["changelog_component"] for t in data["tests"]} == {
            "PostgreSQL", "Файловые системы", "Системные службы", "UnixBench",
            "FreeIPA", "Parsec", "Apache", "Docker/Podman/LXC",
            "Qemu/KVM/Libvirt", "Network",
        }

        async with AsyncSessionLocal() as db:
            ext4 = await test_definition_repo.get_by_code(db, "file_systems.ext4")
            memleak = await test_definition_repo.get_by_code(db, "kernel.xfs_memleak")
        assert ext4.matrix_label == "FS_EXT4"
        assert ext4.changelog_component == "Файловые системы"
        # Ветка `kernel`, но changelog отслеживает его как системную службу —
        # компонент и категория это разные вещи.
        assert memleak.matrix_label == "XFS_mem_leak"
        assert memleak.category == "kernel"
        assert memleak.changelog_component == "Системные службы"

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

    async def test_catalog_carries_legacy_pinning_for_58_of_62(self):
        """Сами данные привязки: 58 тестов из 62 имеют легаси-стенд.

        Четыре исключения — tantor vanilla/kernels и оба overflow: их нет в
        `stands_groups` allta_app, то есть привязки не было и в легаси.
        """
        data = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))
        with_token = [t["code"] for t in data["tests"] if t.get("pinned_stand_token")]
        without = sorted(t["code"] for t in data["tests"] if not t.get("pinned_stand_token"))
        assert len(with_token) == 58
        assert without == [
            "overflow.ram", "overflow.storage_drive",
            "postgresql.tantor_kernels", "postgresql.tantor_vanilla",
        ]
