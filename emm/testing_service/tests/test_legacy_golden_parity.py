"""golden-тесты паритета с allta_app (принцип 3 плана).

Для каждого из 62 тестов НАСТОЯЩЕГО каталога (`scripts/import_catalog.allta.yaml`)
сравнивается то, что собирает testing_service, с тем, что собрал бы легаси
(`tests/legacy_parity/legacy.py` — дословный порт `allta_back.py`/
`backup_image.py`, без импорта легаси-модулей):

* **dates** — `shlex.split` содержимого `dates.conf` из задания воркеру против
  `shlex.split` каждой легаси-строки `dates`, с которой запускался конечный
  скрипт. Сравнение — мультимножества пар «флаг → значение» и токенов:
  порядок флагов argparse не важен;
* **команда запуска** — `launch_command` задания (профиль запуска)
  против команд `remote_test_run()`. Две известные разницы учтены
  подстановкой: `$2` у `starter.sh` — имя файла с git-токеном, а не сам
  токен (легаси-порту передаётся то же имя), `$3` — имя dates-файла очереди
  (`dates_<queue_item>.conf`) вместо `dates_<стенд>.conf`. Пустой `$5`
  (тест без суффикса) равен отсутствующему: `starter.sh` сравнивает `"$5"`.

Значения переменных фиктивные, но одинаковые с обеих сторон: учётки из
`integration_for_catalog`, id папки Zephyr `CATALOG_FOLDER_TREE_ID`, стенд —
`pinned_stand_token` теста (без привязки — `stand3`), два контекста запуска
(обычная и UU-версия, `orel` и `smolensk`).

Известные расхождения — `xfail(strict=True)` со ссылкой на задачу: когда
задача их закроет, тест упадёт как XPASS и запись нужно будет убрать:

* `psql/tantor kernels` — `dates` всех четырёх фаз совпадают по шагам
  теста, но повторные фазы легаси запускает прямой командой
  `run.py`, а у нас — `rerun_script` профиля запуска (команда запуска
  сравнивается только для первой фазы);
* `freeipa.*` — легаси запускает `ipa_run.py {dates}` на хосте ALLTA; у нас
  это последняя строка скрипта профиля «FreeIPA (ipa_run.py)» —
  сравнивается отдельно (`TestFreeipaParity`): каталог, `cd`, интерпретатор
  и argv `dates` строкой.

Импорт каталога занимает ~10 с, поэтому результаты для всех тестов
считаются один раз на модуль (`_GOLDEN`), а параметризованные тесты читают
готовую таблицу.
"""

from __future__ import annotations

import ast
import inspect
import posixpath
import shlex
from collections import Counter
from dataclasses import dataclass

import pytest
import yaml

from src.db.session import AsyncSessionLocal
from src.models import TestStand
from src.repositories import test_definition as test_definition_repo
from src.services import launch_profile as lp_svc
from src.services import queue_steps
from src.services.test_command_arg import resolve_dates, resolve_dates_content
from tests.legacy_parity import legacy
from tests.legacy_parity import (
    LegacyCredentials,
    argv_pairs,
    legacy_parent_page,
    legacy_run,
    short_name_of,
)
from tests.test_launch_context import (  # noqa: F401 — фикстуры каталога
    ALLTA_CATALOG,
    CATALOG_FOLDER_TREE_ID,
    OS_VERSIONS,
    imported_allta_catalog,
    integration_for_catalog,
    mock_server_service,
    os_version_catalog,
    zephyr_folder_for_catalog,
)

CATALOG_TESTS: list[dict] = yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))["tests"]
READINESS = {t["code"]: t["readiness"] for t in CATALOG_TESTS}

CONTEXTS = {
    "1.8-orel": {"RC": "osv_5e46b2d1", "KERNEL": "6.1.0", "MODE": "orel"},
    "uu-smolensk": {"RC": "osv_uu179120", "KERNEL": "5.15.0-70-generic", "MODE": "smolensk"},
}
DEFAULT_STAND = "stand3"
QUEUE_ITEM_ID = "qi_golden"

_KERNELS = (
    "dates четырёх фаз совпадают по шагам (test_dates_match_legacy), но фазы 2-4 легаси — "
    "прямой `cd <ветка> && sudo <venv> run.py -n <dates> -kn kernel` (backup_image.py:919-939), "
    "у нас — `rerun_script` профиля запуска по пути starter.sh; команда запуска шага — не argv легаси"
)
DATES_GAPS: dict[str, str] = {}
LAUNCH_GAPS: dict[str, str] = {}
# FreeIPA: команда сравнивается отдельно (`TestFreeipaParity`) —
# легаси запускал `ipa_run.py {dates}` на хосте ALLTA, у нас это строка
# скрипта профиля «FreeIPA (ipa_run.py)» на стенде-исполнителе сценария.
FREEIPA_TESTS = ("freeipa.auth", "freeipa.create_users", "freeipa.plugin")


def _params(gaps: dict[str, str]) -> list:
    return [
        pytest.param(t["code"], marks=pytest.mark.xfail(strict=True, reason=gaps[t["code"]]))
        if t["code"] in gaps else t["code"]
        for t in CATALOG_TESTS
    ]


# ── расчёт один раз на модуль ────────────────────────────────────────────────

@dataclass
class Golden:
    ours_dates: str
    # dates.conf каждого шага теста; у одношагового — один элемент.
    ours_step_dates: list[str]
    ours_launch: str
    # Текст скрипта запуска первого шага (FreeIPA: строка `ipa_run.py {dates}`).
    ours_script: str
    dates_file: str
    token_file: str
    legacy: legacy.LegacyRun


_GOLDEN: dict[str, dict[str, Golden]] = {}


@pytest.fixture
def golden_source(request):
    """Импортированный каталог и учётки — только если таблица ещё не посчитана.

    Синхронная фикстура: async-фикстуры каталога поднимаются через
    `getfixturevalue` лишь при первом обращении, остальные тесты модуля
    обходятся без импорта.
    """
    if _GOLDEN:
        return None
    return request.getfixturevalue("imported_allta_catalog"), request.getfixturevalue("integration_for_catalog")


def _stand_for(test_item: dict, catalog_stand: TestStand) -> TestStand:
    token = test_item.get("pinned_stand_token") or DEFAULT_STAND
    return TestStand(
        id=f"stand_golden_{token}", server_id=catalog_stand.server_id,
        department_id=catalog_stand.department_id, legacy_token=token,
        queue_enabled=True, is_active=True,
    )


async def _ours(db, test, stand: TestStand, ctx: dict, steps: list, index: int = 0) -> tuple[str, str, str, str, str]:
    """dates.conf и команда запуска шага так, как их собирает `queue.claim_next`
    (`services/queue.py`: контекст резолва с `TEST_BRANCH`/`STARTER_SUFFIX`
    шага, профиль запуска, `resolve_dates` шага в том же контексте,
    `build_launch`; шаг `rerun` — без токена, скрипт — `rerun_script`)."""
    step = steps[index]
    rctx = lp_svc.new_resolve_context(db, test, stand, ctx, debug=False, step=step, steps=steps, step_index=index)
    version = await lp_svc.effective_version(db, test, stand.department_id)
    paths = await lp_svc.render_paths(rctx, version, QUEUE_ITEM_ID)
    content, masked = await resolve_dates(db, test.id, ctx, stand=stand, context=rctx, step_id=step.id)
    rerun = queue_steps.is_rerun(step)
    launch = await lp_svc.build_launch(
        rctx, version, paths, dates_content=content, dates_content_masked=masked,
        git_token="" if rerun else "git-token-secret", testenv_on=False, prepare_only=False, rerun=rerun,
    )
    dates_file = next(f["content"] for f in launch.files if f["path"] == paths.dates)
    script = next(f["content"] for f in launch.files if f["path"] == paths.script)
    return (
        dates_file, launch.launch_command, posixpath.basename(paths.dates), posixpath.basename(paths.token), script,
    )


async def _golden(source) -> dict[str, dict[str, Golden]]:
    if _GOLDEN:
        return _GOLDEN
    catalog_stand, creds = source
    conf_login, conf_token = creds["cred_conf"]
    _jira_login, jira_token = creds["cred_jira"]
    table: dict[str, dict[str, Golden]] = {}
    async with AsyncSessionLocal() as db:
        for item in CATALOG_TESTS:
            test = await test_definition_repo.get_by_code(db, item["code"])
            stand = _stand_for(item, catalog_stand)
            steps = await queue_steps.load_steps(db, test.id)
            per_ctx: dict[str, Golden] = {}
            for label, ctx in CONTEXTS.items():
                dates_file, launch, dates_name, token_name, script = await _ours(db, test, stand, ctx, steps)
                step_dates = [dates_file] + [
                    (await _ours(db, test, stand, ctx, steps, index))[0] for index in range(1, len(steps))
                ]
                release = OS_VERSIONS[ctx["RC"]].name
                short = short_name_of(test.full_name)
                run = legacy_run(
                    test=short, release=release, mode=ctx["MODE"], kernel=ctx["KERNEL"],
                    stand=stand.legacy_token, tcase=test.full_name, cti=CATALOG_FOLDER_TREE_ID,
                    parent_page=legacy_parent_page(release, short),
                    # `$2` у starter.sh после — имя файла с токеном.
                    creds=LegacyCredentials(
                        username=conf_login, conf_token=conf_token, jira_token=jira_token, git_token=token_name,
                    ),
                )
                per_ctx[label] = Golden(dates_file, step_dates, launch, script, dates_name, token_name, run)
            table[item["code"]] = per_ctx
    _GOLDEN.update(table)
    return _GOLDEN


# ── сравнение ────────────────────────────────────────────────────────────────

def _diff(ours: Counter, theirs: Counter) -> str:
    return (
        f"только у нас: {sorted((ours - theirs).elements(), key=repr)}; "
        f"только в легаси: {sorted((theirs - ours).elements(), key=repr)}"
    )


def _launch_argv(command: str, *, legacy_dates_name: str | None = None, dates_name: str = "") -> list[str]:
    argv = shlex.split(command)
    if legacy_dates_name is not None:
        argv = [dates_name if tok == legacy_dates_name else tok for tok in argv]
    while argv and argv[-1] == "":
        argv.pop()
    return argv


class TestCoverage:
    def test_every_catalog_test_is_compared(self):
        assert len(CATALOG_TESTS) == 62
        assert len({t["code"] for t in CATALOG_TESTS}) == 62

    def test_ready_tests_have_no_known_gaps(self):
        """Golden-тест покрывает все `ready`-тесты без xfail."""
        assert {code for code in {**DATES_GAPS, **LAUNCH_GAPS} if READINESS[code] == "ready"} == set()


class TestGoldenParity:
    @pytest.mark.parametrize("code", _params(DATES_GAPS))
    async def test_dates_match_legacy(self, golden_source, code):
        for label, g in (await _golden(golden_source))[code].items():
            ours = [shlex.split(d) for d in g.ours_step_dates]
            theirs = [shlex.split(d) for d in g.legacy.dates_seen]
            assert len(ours) == len(theirs), f"{label}: запусков у нас {len(ours)}, в легаси {len(theirs)}"
            for mine, legacy_argv in zip(ours, theirs):
                assert argv_pairs(mine) == argv_pairs(legacy_argv), \
                    f"{label}: {_diff(argv_pairs(mine), argv_pairs(legacy_argv))}"
                assert sorted(mine) == sorted(legacy_argv), label

    @pytest.mark.parametrize("code", [c for c in _params(LAUNCH_GAPS) if c not in FREEIPA_TESTS])
    async def test_launch_command_matches_legacy(self, golden_source, code):
        for label, g in (await _golden(golden_source))[code].items():
            ours = [("stand", _launch_argv(g.ours_launch))]
            events = [(e.where, e.command) for e in g.legacy.events]
            if len(g.ours_step_dates) > 1:
                # Многоступенчатый тест: _KERNELS — сравнивается запуск
                # первой фазы; повторные фазы легаси — прямой `run.py`, у нас —
                # `rerun_script` профиля; запусков скрипта — по одному на шаг
                # (grub/перезагрузки между фазами — `stand_setup` шагов).
                runs = [e for e in events if "starter.sh" in e[1] or "run.py" in e[1]]
                assert len(runs) == len(g.ours_step_dates), label
                events = [e for e in runs if "starter.sh" in e[1]]
            theirs = [
                (where, _launch_argv(cmd, legacy_dates_name=g.legacy.dates_name, dates_name=g.dates_file))
                for where, cmd in events
            ]
            assert ours == theirs, label

    async def test_kernels_catalog_is_the_first_phase(self, golden_source):
        """Что уже совпадает у `psql/tantor kernels`: dates каталога — фаза
        `begin` (maxcpus=8) легаси, команда — `starter.sh … kernel`."""
        golden = await _golden(golden_source)
        for code in ("postgresql.kernels", "postgresql.tantor_kernels"):
            for label, g in golden[code].items():
                first = shlex.split(g.legacy.dates_seen[0])
                assert argv_pairs(shlex.split(g.ours_dates)) == argv_pairs(first), f"{code} {label}"
                first_start = next(e.command for e in g.legacy.events if "starter.sh" in e.command)
                assert _launch_argv(g.ours_launch) == _launch_argv(
                    first_start, legacy_dates_name=g.legacy.dates_name, dates_name=g.dates_file,
                ), f"{code} {label}"

    async def test_dates_file_is_what_resolve_dates_content_returns(self, golden_source, imported_allta_catalog):
        """Файл в задании воркеру — та же строка, что `resolve_dates_content`
        (им пользуются превью и прочие тесты каталога)."""
        golden = await _golden(golden_source)
        item = next(t for t in CATALOG_TESTS if t["code"] == "file_systems.xfs")
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, item["code"])
            content = await resolve_dates_content(
                db, test.id, CONTEXTS["1.8-orel"], stand=_stand_for(item, imported_allta_catalog),
            )
        assert content == golden["file_systems.xfs"]["1.8-orel"].ours_dates


# ── порт дословный ───────────────────────────────────────────────────────────

def _defs(tree: ast.AST) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _port_body() -> list[ast.stmt]:
    """Перенесённые операторы `_run_backup_image`: от заглушек до `remote_test_run()`."""
    tree = ast.parse(inspect.getsource(legacy._run_backup_image))
    body = tree.body[0].body
    start = next(i for i, s in enumerate(body) if isinstance(s, ast.FunctionDef) and s.name == "GrubCommand") + 1
    end = next(
        i for i, s in enumerate(body)
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and ast.unparse(s.value) == "remote_test_run()"
    )
    return body[start:end]


class TestPortIsVerbatim:
    """Порт в `tests/legacy_parity/legacy.py` совпадает с легаси по AST (комментарии и
    форматирование не в счёт). Правка порта «под новую систему» здесь упадёт."""

    def test_backup_image_statements(self):
        source = legacy.legacy_ast(legacy.BACKUP_IMAGE)
        legacy_stmts = {ast.dump(s) for s in source.body}
        ported = _port_body()
        foreign = [ast.unparse(s).splitlines()[0] for s in ported if ast.dump(s) not in legacy_stmts]
        assert foreign == []
        names = {s.name for s in ported if isinstance(s, ast.FunctionDef)}
        assert names == {"db_kernel_changer", "freeipa_authentication_test", "remote_test_run"}
        # Вся цепочка if/elif `dates` (backup_image.py:333-424) — одним оператором.
        chain = next(
            s for s in source.body
            if isinstance(s, ast.If) and isinstance(s.body[0], ast.Assign) and ast.unparse(s.body[0].targets[0]) == "dates"
        )
        assert ast.dump(chain) in {ast.dump(s) for s in ported}

    def test_build_command_args(self):
        theirs = _defs(legacy.legacy_ast(legacy.ALLTA_BACK))["build_command_args"]
        ours = _defs(ast.parse(inspect.getsource(legacy.build_command_args)))["build_command_args"]
        assert ast.dump(ours) == ast.dump(theirs)

    def test_parent_page_list(self):
        theirs = _defs(legacy.legacy_ast(legacy.IMAGE_CONF))["parent_page_list"]
        ours = _defs(ast.parse(inspect.getsource(legacy.legacy_parent_page)))["parent_page_list"]
        assert ast.dump(ours) == ast.dump(theirs)

    def test_backup_image_argparse_is_complete(self):
        """argparse `backup_image.py` собран из исходника целиком, без пропусков."""
        parsed = legacy._backup_image_parser()
        assert parsed.warnings == []
        assert parsed.arguments == 34


class TestFreeipaParity:
    """`ipa_run.py {dates}` легаси (`backup_image.py:943-964`) = строка
    скрипта профиля FreeIPA; клон — ветка `freeipa` в `…/gitipa/stress_test`."""

    @pytest.mark.parametrize("code", FREEIPA_TESTS)
    async def test_ipa_run_line_matches_legacy(self, golden_source, code):
        for label, g in (await _golden(golden_source))[code].items():
            host = [e.command for e in g.legacy.events if e.where == "host"]
            assert host[:2] == [
                f"cd /home/u/freeipa_test/gitipa && {legacy.VENV_PATH} git_clone.py",
                "cd /home/u/freeipa_test/gitipa/stress_test && git checkout freeipa",
            ], label
            legacy_cd, legacy_dates = host[2].split(" ipa_run.py ", 1)
            ours_line = next(
                line for line in g.ours_script.splitlines() if line.startswith("cd ") and " ipa_run.py " in line
            )
            ours_cd, ours_dates = ours_line.split(" ipa_run.py ", 1)
            assert ours_cd == legacy_cd, label
            assert argv_pairs(shlex.split(ours_dates)) == argv_pairs(shlex.split(legacy_dates)), label
            assert sorted(shlex.split(ours_dates)) == sorted(shlex.split(legacy_dates)), label
            # `git_clone.py` + `checkout freeipa` — клон ветки теста в gitipa.
            assert shlex.split(g.ours_launch)[-1] == "freeipa", label
            assert "cd /home/u/freeipa_test/gitipa\n" in g.ours_script, label

