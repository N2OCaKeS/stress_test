"""Механика `scripts/check_branch_argparse.py` на синтетических ветках.

Сам скрипт гоняется руками против распакованных веток `stress_test` (в
репозиторий `emm` они не входят). Здесь проверяется то, от чего зависит его
вердикт: сборка argparse из исходника, выбор конечного скрипта через `run.py`
(в том числе диспетчер `postgresql` по суффиксу и `parse_args` из локального
модуля, как у `cluster_file_systems`), сборка `dates` из каталога и отчёт.
Устройство синтетических веток — по разделам 1 и 3 `README_TEST_BRANCHES_AUDIT.md`.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from scripts import check_branch_argparse as cba
from tests.test_launch_context import ALLTA_CATALOG

# `run.py` веток: dates-файл → `subprocess.run(f'sudo {VENV_PATH} <скрипт> {dates}', shell=True)`.
RUN_PY = """\
import argparse
import subprocess
from {conf} import VENV_PATH

parser = argparse.ArgumentParser()
parser.add_argument('-n', action='store', required=True, dest='NAME')
args = parser.parse_args()
dates = open(f'/home/u/{{args.NAME}}').read()
subprocess.run(f'sudo {{VENV_PATH}} {script} {{dates}}', shell=True)
"""

POSTGRESQL_RUN_PY = """\
import argparse
import subprocess
from psb_conf import VENV_PATH

parser = argparse.ArgumentParser()
parser.add_argument('-n', action='store', required=True, dest='NAME')
parser.add_argument('-kn', action='store', required=False, dest='KN')
parser.add_argument('-bl', action='store', required=False, dest='BL')
parser.add_argument('-oom', action='store', required=False, dest='OOM')
args = parser.parse_args()
dates = open(f'/home/u/{args.NAME}').read()
if args.KN:
    subprocess.run(f'sudo {VENV_PATH} diff_kernel_quantity.py {dates}', shell=True)
elif args.BL:
    subprocess.run(f'sudo {VENV_PATH} bl_run.py {dates}', shell=True)
elif args.OOM:
    subprocess.run(f'sudo {VENV_PATH} oom_run.py {dates}', shell=True)
else:
    subprocess.run(f'sudo {VENV_PATH} psb_run.py {dates}', shell=True)
    subprocess.run(f'sudo {VENV_PATH} psb_public.py', shell=True)
"""

COMMON_ARGS = """\
parser.add_argument('-u', '--username', action='store', required=True, dest='USER')
parser.add_argument('-t', '--token', action='store', required=True, dest='TOKEN')
parser.add_argument('-cs', '--confluence-space', action='store', required=True, dest='SPACE')
parser.add_argument('-cpp', '--confluence-parent-page', action='store', required=True, dest='PPAGE')
parser.add_argument('-cnp', '--confluence-new-page', action='store', required=True, dest='NPAGE')
parser.add_argument('-fti', action='store', required=True, dest='FTI')
parser.add_argument('-tcyc', action='store', required=True, dest='TCYC')
parser.add_argument('-tcas', action='store', required=True, dest='TCAS')
parser.add_argument('-ba', action='store', required=True, dest='BA')
parser.add_argument('-tcv', action='store', required=True, dest='TCV')
"""

FSB_RUN_PY = f"""\
import argparse
import os

parser = argparse.ArgumentParser()
{COMMON_ARGS}parser.add_argument('-sn', action='store', required=True, choices=['3', '10'], dest='SN')
parser.add_argument('-fs', action='store', required=True, choices=['xfs', 'ext4'], dest='FS')
parser.add_argument('--parsec', action='store_true', dest='PARSEC')
parser.add_argument('-o', action='store', default=os.getcwd(), help=f'out {{os.sep}}', dest='OUT')
args = parser.parse_args()
"""

PSB_RUN_PY = f"""\
import argparse

def get_args():
    parser = argparse.ArgumentParser()
{textwrap.indent(COMMON_ARGS, '    ')}    parser.add_argument('-sn', action='store', required=True, choices=['1', '3', '4', '12', '14'], dest='SN')
    parser.add_argument('-db', action='store_true', dest='DB')
    parser.add_argument('-c', action='store_true', dest='C')
    parser.add_argument('-pack', '--package', action='store', required=True, dest='PACK')
    return parser.parse_args()
"""

DIFF_KERNEL_PY = f"""\
import argparse
parser = argparse.ArgumentParser()
{COMMON_ARGS}parser.add_argument('-sn', action='store', required=True, dest='SN')
parser.add_argument('-q', action='store', type=int, required=True, dest='Q')
parser.add_argument('-sf', action='store', choices=['begin', 'end'], dest='SF')
parser.add_argument('--package', action='store', required=True, dest='PACK')
parser.add_argument('-db', action='store', dest='DB')
"""

# cluster_file_systems: argparse вынесен в libs/libparseargs.py.
CFS_RUN_PY = """\
from libs.libparseargs import parse_args
from cfs_conf import HOST_IP

args = parse_args()
"""

LIBPARSEARGS_PY = f"""\
import argparse

def parse_args():
    parser = argparse.ArgumentParser()
{textwrap.indent(COMMON_ARGS, '    ')}    parser.add_argument('-sn', action='store', required=True, dest='SN')
    parser.add_argument('-fs', action='store', required=True, choices=['ocfs2', 'ceph'], dest='FS')
    parser.add_argument('-vbox', action='store', required=True, dest='VBOX')
    parser.add_argument('-kernel', action='store', required=True, dest='KERNEL')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--libvirt', action='store_true', dest='LIBVIRT')
    mode.add_argument('--parsec', action='store_true', dest='PARSEC')
    parser.add_argument('--test-set', action='store', choices=['fio'], dest='SET')
    return parser.parse_args()
"""

IPA_RUN_PY = f"""\
import argparse
parser = argparse.ArgumentParser()
{COMMON_ARGS}parser.add_argument('-sn', action='store', required=True, choices=[str(i) for i in range(1, 14)], dest='SN')
parser.add_argument('-tt', action='store', required=True, choices=['auth', 'create-users', 'plugin'], dest='TT')
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def branches(tmp_path: Path) -> Path:
    root = tmp_path / "branches"
    _write(root / "file_systems" / "run.py", RUN_PY.format(conf="fsb_conf", script="fsb_run.py"))
    _write(root / "file_systems" / "fsb_run.py", FSB_RUN_PY)
    _write(root / "postgresql" / "run.py", POSTGRESQL_RUN_PY)
    _write(root / "postgresql" / "psb_run.py", PSB_RUN_PY)
    _write(root / "postgresql" / "psb_public.py", "import json\n")
    _write(root / "postgresql" / "diff_kernel_quantity.py", DIFF_KERNEL_PY)
    _write(root / "postgresql" / "bl_run.py", DIFF_KERNEL_PY)
    _write(root / "postgresql" / "oom_run.py", DIFF_KERNEL_PY)
    _write(root / "cluster_file_systems" / "run.py", RUN_PY.format(conf="cfs_conf", script="cfs_run.py"))
    _write(root / "cluster_file_systems" / "cfs_run.py", CFS_RUN_PY)
    _write(root / "cluster_file_systems" / "libs" / "libparseargs.py", LIBPARSEARGS_PY)
    _write(root / "freeipa" / "ipa_run.py", IPA_RUN_PY)
    _write(root / "freeipa" / "prepare.sh", "#!/bin/bash\n")
    return root


CATALOG = {t["code"]: t for t in yaml.safe_load(ALLTA_CATALOG.read_text(encoding="utf-8"))["tests"]}
SUFFIX_FLAGS = cba.starter_suffix_flags(cba.DEFAULT_STARTER)


def _check(test: dict, root: Path, **over) -> cba.Result:
    kwargs = dict(
        rc="1.8.1.6", mode=None, kernel="6.1.90-1-generic", default_stand="stand3",
        suffix_flags=SUFFIX_FLAGS, overrides={}, extra_values={},
    )
    kwargs.update(over)
    return cba.check_test(test, root, **kwargs)


class TestParserFromSource:
    def test_required_choices_and_flags(self, branches):
        parsed = cba.parser_from_source(branches / "file_systems" / "fsb_run.py")
        assert parsed.arguments == 14
        # `default=os.getcwd()` не литерал — пропущен с предупреждением, `help` — молча.
        assert parsed.warnings == ["fsb_run.py:18: default=os.getcwd() не литерал — не проверяется"]
        common = ["-u", "x", "-t", "y", "-cs", "S", "-cpp", "a b", "-cnp", "n", "-fti", "1", "-tcyc", "c",
                  "-tcas", "case name", "-ba", "b", "-tcv", "1.8.1.6"]
        ns = parsed.parse(common + ["-sn", "3", "-fs", "xfs", "--parsec"])
        assert (ns.SN, ns.FS, ns.PARSEC, ns.PPAGE) == ("3", "xfs", True, "a b")
        with pytest.raises(cba.ArgsError, match="invalid choice"):
            parsed.parse(common + ["-sn", "5", "-fs", "xfs"])
        with pytest.raises(cba.ArgsError, match="required: -fs"):
            parsed.parse(common + ["-sn", "3"])
        with pytest.raises(cba.ArgsError, match="unrecognized arguments: benchmark"):
            parsed.parse(common + ["-sn", "3", "-fs", "xfs", "-tcas", "case", "benchmark"])

    def test_parser_inside_function_and_mutually_exclusive_group(self, branches):
        parsed = cba.parser_from_source(branches / "cluster_file_systems" / "libs" / "libparseargs.py")
        assert parsed.warnings == []
        base = ["-u", "x", "-t", "y", "-cs", "S", "-cpp", "p", "-cnp", "n", "-fti", "1", "-tcyc", "c",
                "-tcas", "t", "-ba", "b", "-tcv", "1.8", "-sn", "10", "-fs", "ocfs2", "-vbox", "1.8", "-kernel", "k"]
        assert parsed.parse(base + ["--libvirt"]).LIBVIRT is True
        with pytest.raises(cba.ArgsError, match="not allowed with argument"):
            parsed.parse(base + ["--libvirt", "--parsec"])

    def test_type_int_is_applied(self, branches):
        parsed = cba.parser_from_source(branches / "postgresql" / "diff_kernel_quantity.py")
        with pytest.raises(cba.ArgsError, match="invalid int value"):
            parsed.parse(["-q", "eight"])

    def test_legacy_backup_image_is_parsed_completely(self):
        parsed = cba.parser_from_source(cba.SERVICE_DIR.parent / "allta_app_full" / "backup_image.py")
        assert (parsed.arguments, parsed.warnings) == (34, [])


class TestTargetScript:
    def test_starter_suffix_flags_from_legacy_starter(self):
        assert SUFFIX_FLAGS == {"kernel": "-kn", "balance": "-bl", "oom": "-oom"}

    def test_single_script_branch(self, branches):
        script, note = cba.find_target_script(branches / "file_systems", None, SUFFIX_FLAGS)
        assert (script.name, note) == ("fsb_run.py", "")

    @pytest.mark.parametrize(("suffix", "expected"), [
        (None, "psb_run.py"),
        ("kernel", "diff_kernel_quantity.py"),
        ("balance", "bl_run.py"),
        ("oom", "oom_run.py"),
    ])
    def test_postgresql_dispatch_by_suffix(self, branches, suffix, expected):
        script, _note = cba.find_target_script(branches / "postgresql", suffix, SUFFIX_FLAGS)
        assert script.name == expected

    def test_unknown_suffix_is_an_error(self, branches):
        with pytest.raises(LookupError, match="суффикс 'nope'"):
            cba.find_target_script(branches / "postgresql", "nope", SUFFIX_FLAGS)

    def test_branch_without_run_py(self, branches):
        script, note = cba.find_target_script(branches / "freeipa", None, SUFFIX_FLAGS)
        assert script.name == "ipa_run.py" and "нет run.py" in note

    def test_parse_args_from_local_module(self, branches):
        branch = branches / "cluster_file_systems"
        parsed = cba.script_parser(branch / "cfs_run.py", branch)
        assert parsed.source == branch / "libs" / "libparseargs.py"
        assert parsed.arguments > 10


class TestCheckCatalogTests:
    @pytest.mark.parametrize("code", [
        "file_systems.xfs", "file_systems.xfs_parsec", "postgresql.base", "postgresql.kernels",
        "postgresql.tantor_kernels", "cluster_file_systems.ocfs2", "cluster_file_systems.ceph_fio",
        "freeipa.create_users",
    ])
    def test_real_catalog_entries_pass(self, branches, code):
        result = _check(CATALOG[code], branches)
        assert result.ok, result.message

    def test_cfs_reports_where_argparse_came_from(self, branches):
        result = _check(CATALOG["cluster_file_systems.ceph"], branches)
        assert result.script == "cluster_file_systems/cfs_run.py (argparse: libs/libparseargs.py)"

    def test_choices_violation_is_an_error(self, branches):
        # stand4 → `-sn 4`, а fsb_run.py синтетической ветки знает только 3 и 10.
        result = _check({**CATALOG["file_systems.xfs"], "pinned_stand_token": "stand4"}, branches)
        assert not result.ok and "invalid choice: '4'" in result.message

    def test_raw_quoting_breaks_on_spaces(self, branches):
        """Без экранирования заголовок с пробелами разваливается (раздел 4.1 аудита)."""
        result = _check({**CATALOG["file_systems.xfs"], "dates_quoting": "raw"}, branches)
        assert not result.ok and "unrecognized arguments" in result.message

    def test_legacy_smolensk_flags_are_rejected(self, branches):
        """Каталог до: `-fs postgresql-sm` у `postgresql.smolensk` (шаг 16 аудита)."""
        test = {**CATALOG["postgresql.base"], "command": [
            *CATALOG["postgresql.base"]["command"][:18],
            {"kind": "literal", "literal_value": "-fs"}, {"kind": "literal", "literal_value": "postgresql-sm"},
        ]}
        result = _check(test, branches)
        assert not result.ok and "required" in result.message

    def test_missing_branch_and_unknown_variable(self, branches):
        assert "нет каталога ветки" in _check(CATALOG["virt.fio"], branches).message
        test = {**CATALOG["file_systems.xfs"], "command": [{"kind": "variable", "variable_code": "NOPE"}]}
        assert "NOPE" in _check(test, branches).message

    def test_script_override(self, branches):
        result = _check(CATALOG["postgresql.kernels"], branches, overrides={"postgresql:kernel": "bl_run.py"})
        assert result.ok and result.script == "postgresql/bl_run.py"

    def test_sample_values_cover_every_catalog_variable(self):
        for test in CATALOG.values():
            values = cba.sample_values(test, rc="1.8.1.6", mode="orel", kernel="6.1", stand="stand12")
            argv = cba.catalog_argv(test, values)
            assert "-tcv" in argv, test["code"]
            if "-sn" in argv:
                assert argv[argv.index("-sn") + 1] == "12", test["code"]


def test_main_prints_report_and_exit_code(branches, tmp_path, capsys):
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(yaml.safe_dump({"tests": [
        CATALOG["file_systems.xfs"], CATALOG["postgresql.base"], {**CATALOG["file_systems.ext4"], "pinned_stand_token": "stand4"},
    ]}, allow_unicode=True), encoding="utf-8")
    assert cba.main([str(branches), "--catalog", str(catalog)]) == 1
    out = capsys.readouterr().out
    assert "OK    file_systems.xfs " in out
    assert "OK    postgresql.base " in out
    assert "ERROR file_systems.ext4 " in out and "invalid choice: '4'" in out
    assert "Итого: 2/3 OK" in out
    assert cba.main([str(branches), "--catalog", str(catalog), "--only", "file_systems.xfs"]) == 0
