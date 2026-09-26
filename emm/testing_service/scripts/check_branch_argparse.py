#!/usr/bin/env python3
"""argparse-проверка каталога против веток `stress_test`.

Для каждого теста каталога (`import_catalog.allta.yaml`) скрипт:

1. находит конечный скрипт ветки — тот, который `run.py` ветки зовёт через
   `subprocess.run(f'sudo {VENV_PATH} <скрипт>.py {dates}', shell=True)`.
   Если `run.py` зовёт несколько скриптов (ветка `postgresql`: `-kn` →
   `diff_kernel_quantity.py`, `-bl` → `bl_run.py`, `-oom` → `oom_run.py`,
   иначе `psb_run.py`), нужный выбирается по `starter_suffix` теста: флаг,
   которым `starter.sh` передаёт суффикс в `run.py`, берётся из самого
   `starter.sh`, dest флага — из argparse `run.py`, ветка `if` — по этому
   dest. У ветки без `run.py` (`freeipa`) — единственный `*_run.py`;
2. собирает настоящий `argparse.ArgumentParser` из вызовов
   `add_argument(...)` в исходнике скрипта (AST, без импорта: скрипты веток
   при импорте ходят в сеть). Если в скрипте их нет, а `parse_args`
   импортирован из локального модуля ветки (`cluster_file_systems`:
   `libs/libparseargs.py`), берётся этот модуль;
3. собирает `dates` из слотов каталога с фиктивными значениями переменных
   (`SAMPLE_VALUES`, `--var CODE=VALUE`), склеивает по `dates_quoting` теста
   и разбирает `shlex.split` — так же, как shell в `run.py`;
4. печатает OK/ERROR по тестам и итог. Код выхода 0 — все OK.

Проверяется только разбор аргументов (обязательные флаги, `choices`,
неизвестные флаги), не логика тестов. Не часть CI: ветки `stress_test` в
репозиторий `emm` не входят.

Получить ветки (только чтение, без checkout; из любого клона `stress_test`):

    mkdir -p /tmp/branches
    for b in apache2 astra_openvpn astraevents auditd cluster_file_systems \\
             docker exim file_systems freeipa kernel linux_system network \\
             overflow parsec postgresql syslog_ng virt; do
        git fetch origin "$b"
        git archive FETCH_HEAD "$b/" | tar -x -C /tmp/branches
    done

У каждой тестовой ветки код лежит в каталоге `<ветка>/` в корне, так что
получится `/tmp/branches/<ветка>/run.py`. Запуск:

    cd emm/testing_service
    python scripts/check_branch_argparse.py /tmp/branches
    python scripts/check_branch_argparse.py /tmp/branches --rc 1.7.9.UU.1.2 --only postgresql.base
    python scripts/check_branch_argparse.py /tmp/branches \\
        --script postgresql:kernel=diff_kernel_quantity.py   # если run.py не разобрался

Зависимости — только стандартная библиотека и PyYAML (есть в venv сервиса).
"""

from __future__ import annotations

import argparse
import ast
import re
import shlex
import sys
import warnings as _warnings
from dataclasses import dataclass, field
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = SERVICE_DIR / "scripts" / "import_catalog.allta.yaml"
# Легаси `starter.sh` — он же сид профиля запуска `lp_default`: из него
# берётся соответствие «суффикс → флаг `run.py`» (`starter.sh`, блок `$5`).
DEFAULT_STARTER = SERVICE_DIR.parent / "allta_app_full" / "starter.sh"

_SCRIPT_RE = re.compile(r"([\w./-]+\.py)\b")
_STARTER_SUFFIX_RE = re.compile(r'"\$5"\s*==\s*"([^"]+)".*?\n\s*python3?\s+run\.py\b[^\n]*?\s(-[\w-]+)\s+"\$5"', re.S)
_BUILTIN_TYPES = {"int": int, "float": float, "str": str}


class ArgsError(Exception):
    """argparse отверг аргументы (вместо `SystemExit` с кодом 2)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D401 — сигнатура argparse
        raise ArgsError(message)

    def exit(self, status=0, message=None):
        raise ArgsError((message or "").strip() or f"exit {status}")


# ── argparse из исходника ────────────────────────────────────────────────────

@dataclass
class SourceParser:
    """argparse, собранный из `add_argument(...)` одного модуля."""

    parser: argparse.ArgumentParser
    source: Path
    arguments: int
    warnings: list[str] = field(default_factory=list)

    def parse(self, argv: list[str]) -> argparse.Namespace:
        return self.parser.parse_args(argv)


def _parse(path: Path) -> ast.Module:
    # В исходниках веток и легаси встречаются `'\(...'` в обычных строках
    # (sed-выражения) — SyntaxWarning при разборе к проверке не относится.
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_call_to(node: ast.AST, name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == name) or (isinstance(func, ast.Name) and func.id == name)


def _literal_kwargs(call: ast.Call, warnings: list[str], where: str) -> dict:
    kwargs: dict = {}
    for kw in call.keywords:
        if kw.arg is None:
            warnings.append(f"{where}: **kwargs пропущены")
            continue
        value = kw.value
        if kw.arg == "type" and isinstance(value, ast.Name) and value.id in _BUILTIN_TYPES:
            kwargs["type"] = _BUILTIN_TYPES[value.id]
            continue
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "argparse":
            attr = getattr(argparse, value.attr, None)
            if attr is not None:
                kwargs[kw.arg] = attr
                continue
        try:
            kwargs[kw.arg] = ast.literal_eval(value)
        except ValueError:
            if kw.arg in ("help", "metavar"):
                continue
            warnings.append(f"{where}: {kw.arg}={ast.unparse(value)} не литерал — не проверяется")
    return kwargs


def _receiver(call: ast.Call) -> str:
    return ast.unparse(call.func.value) if isinstance(call.func, ast.Attribute) else ""


def parser_from_source(path: Path) -> SourceParser:
    """Собрать argparse из всех `X.add_argument(...)` модуля `path`.

    Вызовы группируются по парсеру: `P = ArgumentParser(...)`, группы `G =
    P.add_mutually_exclusive_group(...)`/`add_argument_group(...)` относятся к
    `P`. Если парсеров в модуле несколько, берётся тот, у которого больше
    аргументов (с предупреждением). Нелитеральные значения (`default=os.getcwd()`,
    `type=some_func`) отбрасываются с предупреждением — `help`/`metavar` молча.
    """
    tree = _parse(path)
    roots: dict[str, str] = {}          # имя переменной → имя корневого парсера
    parser_kwargs: dict[str, ast.Call] = {}
    groups: dict[str, tuple[str, ast.Call]] = {}
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.value, ast.Call):
            target = ast.unparse(node.targets[0])
            if _is_call_to(node.value, "ArgumentParser"):
                roots[target] = target
                parser_kwargs[target] = node.value
            elif _is_call_to(node.value, "add_mutually_exclusive_group") or _is_call_to(node.value, "add_argument_group"):
                groups[target] = (_receiver(node.value), node.value)
        if _is_call_to(node, "add_argument") and isinstance(node.func, ast.Attribute):
            calls.append(node)
    calls.sort(key=lambda c: (c.lineno, c.col_offset))

    def root_of(name: str) -> str:
        seen = set()
        while name in groups and name not in seen:
            seen.add(name)
            name = groups[name][0]
        return roots.get(name, name)

    by_root: dict[str, list[ast.Call]] = {}
    for call in calls:
        by_root.setdefault(root_of(_receiver(call)), []).append(call)
    warnings: list[str] = []
    if not by_root:
        return SourceParser(parser=_Parser(prog=path.name), source=path, arguments=0)
    root, root_calls = max(by_root.items(), key=lambda kv: len(kv[1]))
    if len(by_root) > 1:
        others = ", ".join(f"{name} ({len(c)})" for name, c in by_root.items() if name != root)
        warnings.append(f"в модуле несколько парсеров, взят {root}; прочие: {others}")

    kwargs = {}
    if root in parser_kwargs:
        kwargs = {
            k: v for k, v in _literal_kwargs(parser_kwargs[root], warnings, "ArgumentParser").items()
            if k in ("allow_abbrev", "prefix_chars", "add_help", "argument_default")
        }
    parser = _Parser(prog=path.name, **kwargs)
    built_groups: dict[str, argparse._ActionsContainer] = {}

    def container(name: str) -> argparse._ActionsContainer:
        if name not in groups:
            return parser
        if name not in built_groups:
            parent_name, call = groups[name]
            parent = container(parent_name)
            gkw = _literal_kwargs(call, warnings, name)
            if _is_call_to(call, "add_mutually_exclusive_group"):
                built_groups[name] = parent.add_mutually_exclusive_group(required=bool(gkw.get("required", False)))
            else:
                built_groups[name] = parent.add_argument_group(gkw.get("title"))
        return built_groups[name]

    added = 0
    for call in root_calls:
        where = f"{path.name}:{call.lineno}"
        try:
            flags = [ast.literal_eval(a) for a in call.args]
        except ValueError:
            warnings.append(f"{where}: имя аргумента не литерал — аргумент пропущен")
            continue
        try:
            container(_receiver(call)).add_argument(*flags, **_literal_kwargs(call, warnings, where))
            added += 1
        except (argparse.ArgumentError, TypeError, ValueError) as exc:
            warnings.append(f"{where}: {exc}")
    return SourceParser(parser=parser, source=path, arguments=added, warnings=warnings)


def _local_imports(path: Path, root: Path) -> list[Path]:
    """Модули ветки, импортированные из `path` (`from libs.x import y`, `import libs.x`)."""
    tree = _parse(path)
    found: list[Path] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for name in names:
            rel = Path(*name.split("."))
            for base in (path.parent, root):
                candidate = (base / rel).with_suffix(".py")
                if candidate.is_file() and candidate not in found:
                    found.append(candidate)
    return found


def script_parser(script: Path, branch_dir: Path) -> SourceParser:
    """argparse конечного скрипта; нет своих `add_argument` — из локального модуля."""
    own = parser_from_source(script)
    if own.arguments:
        return own
    candidates = [parser_from_source(m) for m in _local_imports(script, branch_dir)]
    candidates = [c for c in candidates if c.arguments]
    if not candidates:
        return own
    best = max(candidates, key=lambda c: c.arguments)
    if len(candidates) > 1:
        best.warnings.append("argparse найден в нескольких модулях: " + ", ".join(c.source.name for c in candidates))
    return best


# ── какой скрипт зовёт run.py ────────────────────────────────────────────────

def starter_suffix_flags(starter: Path) -> dict[str, str]:
    """`starter.sh`: суффикс (`$5`) → флаг `run.py` (`kernel` → `-kn` …)."""
    if not starter.is_file():
        return {}
    return {suffix: flag for suffix, flag in _STARTER_SUFFIX_RE.findall(starter.read_text(encoding="utf-8"))}


def _scripts_in(nodes: list[ast.AST]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                for name in _SCRIPT_RE.findall(sub.value):
                    name = name.rsplit("/", 1)[-1]
                    if name != "run.py" and name not in names:
                        names.append(name)
    return names


def _if_chains(tree: ast.AST) -> list[list[tuple[ast.expr | None, list[ast.stmt]]]]:
    """Цепочки `if/elif/else`: [(условие | None для else, тело)]."""
    chains = []
    elifs = {id(n.orelse[0]) for n in ast.walk(tree)
             if isinstance(n, ast.If) and len(n.orelse) == 1 and isinstance(n.orelse[0], ast.If)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or id(node) in elifs:
            continue
        chain: list[tuple[ast.expr | None, list[ast.stmt]]] = []
        cur: ast.If | None = node
        while cur is not None:
            chain.append((cur.test, cur.body))
            if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                cur = cur.orelse[0]
            else:
                if cur.orelse:
                    chain.append((None, cur.orelse))
                cur = None
        chains.append(chain)
    return chains


def _mentions(test: ast.expr, names: set[str]) -> bool:
    for sub in ast.walk(test):
        if isinstance(sub, ast.Attribute) and sub.attr in names:
            return True
        if isinstance(sub, ast.Name) and sub.id in names:
            return True
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in names:
            return True
    return False


def find_target_script(
    branch_dir: Path, suffix: str | None, suffix_flags: dict[str, str],
) -> tuple[Path, str]:
    """Конечный скрипт, который `run.py` ветки позовёт для `starter_suffix` теста.

    Возвращает (путь, пометка); не получилось — `LookupError` с причиной.
    """
    run_py = branch_dir / "run.py"
    if not run_py.is_file():
        candidates = sorted(p for p in branch_dir.glob("*_run.py") if p.is_file())
        if len(candidates) == 1:
            return candidates[0], "нет run.py — единственный *_run.py"
        raise LookupError(f"нет run.py, кандидатов *_run.py: {len(candidates)}")

    tree = _parse(run_py)
    scripts = _scripts_in([tree])
    if not scripts:
        raise LookupError("run.py не упоминает ни одного *.py")
    if len(scripts) == 1:
        return branch_dir / scripts[0], ""

    # Несколько скриптов — выбор по суффиксу: флаг из starter.sh → dest в argparse run.py.
    run_parser = parser_from_source(run_py).parser
    dests: dict[str, str] = {}
    for sfx, flag in suffix_flags.items():
        action = run_parser._option_string_actions.get(flag)
        if action is not None:
            dests[sfx] = action.dest
    if suffix and suffix not in dests:
        raise LookupError(f"суффикс {suffix!r}: нет флага в starter.sh или в argparse run.py")
    for chain in _if_chains(tree):
        branches = [(test, body) for test, body in chain if test is None or _mentions(test, set(dests.values()))]
        if not any(test is not None for test, _ in branches):
            continue
        for test, body in branches:
            if suffix is None and test is None:
                picked = _scripts_in(body)
            elif suffix is not None and test is not None and _mentions(test, {dests[suffix]}):
                picked = _scripts_in(body)
            else:
                continue
            if picked:
                return branch_dir / picked[0], f"run.py: ветка по {'else' if test is None else ast.unparse(test)}"
    raise LookupError(f"run.py зовёт {', '.join(scripts)}; не удалось выбрать по суффиксу {suffix!r}")


# ── dates из каталога ────────────────────────────────────────────────────────

def _quote_legacy(token: str) -> str:
    return f'"{token}"' if any(ch.isspace() for ch in token) else token


# Как `services/test_command_arg.join_dates_tokens` (D4) — без импорта `src`:
# скрипт офлайновый и не требует окружения сервиса.
_QUOTERS = {"shell": shlex.quote, "legacy": _quote_legacy, "raw": lambda token: token}


def sample_values(test: dict, *, rc: str, mode: str, kernel: str, stand: str) -> dict[str, str]:
    """Фиктивные, но правдоподобные значения переменных каталога.

    Проверяется разбор аргументов, поэтому значение важно там, где у скрипта
    `choices` (`-sn` — номер стенда) или где shell делит строку (пробелы в
    заголовках и имени тест-кейса). Формы значений — как у сид-переменных
    (миграции `tp01_seed_legacy_formulas`, `tp02_catalog_parity_data`).
    """
    short = test.get("short_name") or test["full_name"]
    return {
        "CONFLUENCE_USER": "conf-bot",
        "CONFLUENCE_TOKEN": "conf-token",
        "CONFLUENCE_SPACE": "DEVQA",
        "JIRA_BASIC_AUTH": "Basic ZmFrZTpmYWtl",
        "PARENT_PAGE": f"STRESS_report {rc} ⬝ {test.get('changelog_component') or ''}",
        "CONFLUENCE_NEW_PAGE": f"{short}_{rc}_{mode}_{kernel}_{stand}",
        "TEST_CYCLE_NAME": f"{rc}_{mode}_{kernel}_{stand}",
        "TEST_CASE_NAME": test["full_name"],
        "FOLDER_TREE_ID": "4242",
        "STAND": "".join(ch for ch in stand if ch.isdigit()),
        "RC_NAME": rc,
        "KERNEL": kernel,
        "MODE": mode,
    }


def _render(template: str, values: dict[str, str]) -> str:
    def sub(match: re.Match) -> str:
        if match.group(1) not in values:
            raise KeyError(match.group(1))
        return values[match.group(1)]
    return re.sub(r"\{([A-Z][A-Z0-9_]*)\}", sub, template)


def catalog_argv(test: dict, values: dict[str, str], step: dict | None = None) -> list[str]:
    """argv конечного скрипта: слоты → `dates` по `dates_quoting` → `shlex.split`.

    У многоступенчатого теста команда шага — общее начало
    `command` теста плюс `command` шага.
    """
    tokens: list[str] = []
    for slot in [*(test.get("command") or []), *((step or {}).get("command") or [])]:
        if slot["kind"] == "literal":
            tokens.append(str(slot["literal_value"]))
            continue
        code = slot["variable_code"]
        if slot.get("override_value") is not None:
            tokens.append(_render(str(slot["override_value"]), values))
        elif code in values:
            tokens.append(values[code])
        else:
            raise KeyError(code)
    quote = _QUOTERS.get(test.get("dates_quoting") or "shell", shlex.quote)
    return shlex.split(" ".join(quote(t) for t in tokens))


# ── проверка ─────────────────────────────────────────────────────────────────

@dataclass
class Result:
    code: str
    ok: bool
    script: str
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def check_test(
    test: dict, branches_dir: Path, *, rc: str, mode: str | None, kernel: str, default_stand: str,
    suffix_flags: dict[str, str], overrides: dict[str, str], extra_values: dict[str, str],
    parsers: dict[Path, SourceParser] | None = None,
) -> Result:
    code = test["code"]
    branch = test.get("category") or ""
    suffix = test.get("starter_suffix") or None
    branch_dir = branches_dir / branch
    if not branch_dir.is_dir():
        return Result(code, False, branch, f"нет каталога ветки {branch_dir}")
    try:
        override = overrides.get(f"{branch}:{suffix}") if suffix else None
        override = override or overrides.get(branch)
        if override:
            script, note = branch_dir / override, "--script"
        else:
            script, note = find_target_script(branch_dir, suffix, suffix_flags)
    except LookupError as exc:
        return Result(code, False, branch, str(exc))
    label = f"{branch}/{script.name}"
    if not script.is_file():
        return Result(code, False, label, "файл не найден")
    cache = parsers if parsers is not None else {}
    if script not in cache:
        cache[script] = script_parser(script, branch_dir)
    parsed = cache[script]
    warnings = list(parsed.warnings) + ([note] if note else [])
    if parsed.source != script:
        label += f" (argparse: {parsed.source.relative_to(branch_dir)})"
    if not parsed.arguments:
        return Result(code, False, label, "argparse не найден ни в скрипте, ни в локальных модулях", warnings)
    stand = test.get("pinned_stand_token") or default_stand
    values = {**sample_values(test, rc=rc, mode=mode or test.get("mode") or "orel", kernel=kernel, stand=stand),
              **extra_values}
    steps = test.get("steps") or [None]
    for index, step in enumerate(steps):
        where = f"шаг {index + 1}/{len(steps)}: " if len(steps) > 1 else ""
        try:
            argv = catalog_argv(test, values, step)
        except KeyError as exc:
            return Result(
                code, False, label, f"{where}нет примерного значения переменной {exc.args[0]} (--var)", warnings,
            )
        try:
            parsed.parse(argv)
        except ArgsError as exc:
            return Result(code, False, label, f"{where}{exc}", warnings)
    return Result(code, True, label, "", warnings)


def _pairs(values: list[str], option: str) -> dict[str, str]:
    result = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise SystemExit(f"{option}: ожидается KEY=VALUE, получено {item!r}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    import yaml

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("branches", type=Path, help="каталог с распакованными ветками (<dir>/<ветка>/run.py)")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="YAML каталога (по умолчанию allta)")
    parser.add_argument("--starter", type=Path, default=DEFAULT_STARTER,
                        help="starter.sh, из которого берётся «суффикс → флаг run.py»")
    parser.add_argument("--rc", default="1.8.1.6", help="имя версии ОС (RC_NAME)")
    parser.add_argument("--mode", default=None, help="режим; по умолчанию mode теста, иначе orel")
    parser.add_argument("--kernel", default="6.1.90-1-generic", help="ядро")
    parser.add_argument("--stand", default="stand3", help="стенд для тестов без pinned_stand_token")
    parser.add_argument("--only", nargs="*", default=None, help="коды тестов")
    parser.add_argument("--script", action="append", default=[],
                        help="ветка[:суффикс]=скрипт.py — явный конечный скрипт, если run.py не разобрался")
    parser.add_argument("--var", action="append", default=[], help="CODE=VALUE — значение переменной")
    parser.add_argument("-v", "--verbose", action="store_true", help="печатать предупреждения разбора")
    args = parser.parse_args(argv)

    if not args.branches.is_dir():
        parser.error(f"нет каталога {args.branches}")
    tests = yaml.safe_load(args.catalog.read_text(encoding="utf-8"))["tests"]
    if args.only:
        tests = [t for t in tests if t["code"] in set(args.only)]
    suffix_flags = starter_suffix_flags(args.starter)
    overrides = _pairs(args.script, "--script")
    extra = _pairs(args.var, "--var")

    parsers: dict[Path, SourceParser] = {}
    results = [
        check_test(
            t, args.branches, rc=args.rc, mode=args.mode, kernel=args.kernel, default_stand=args.stand,
            suffix_flags=suffix_flags, overrides=overrides, extra_values=extra, parsers=parsers,
        )
        for t in tests
    ]
    width = max((len(r.code) for r in results), default=0)
    for r in results:
        status = "OK   " if r.ok else "ERROR"
        line = f"{status} {r.code:<{width}}  {r.script}"
        if r.message:
            line += f": {r.message}"
        print(line)
        if args.verbose:
            for w in r.warnings:
                print(f"      ! {w}")
    ok = sum(r.ok for r in results)
    print(f"\nИтого: {ok}/{len(results)} OK (RC {args.rc}, ядро {args.kernel})")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
