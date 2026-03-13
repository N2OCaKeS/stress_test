from __future__ import annotations
from dataclasses import dataclass
import getpass
import os
import re
import sys
import click

from allta_cli.utils import ui
from allta_cli.utils import auth as auth_utils
from allta_cli.commands.tokens import (
    delete_token_credential_cmd as tokens_delete_run,
    get_token_credential_cmd as tokens_get_run,
    list_token_credentials_cmd as tokens_list_run,
    tokens_cmd as tokens_run,
    update_token_credential_cmd as tokens_update_run,
    upsert_token_credential_cmd as tokens_upsert_run,
)
from allta_cli.commands.files import files_cmd as files_run, boxes_cmd, releases_cmd
from allta_cli.commands.creds import (
    delete_credential_cmd as creds_delete_run,
    get_credential_cmd as creds_get_run,
    list_credentials_cmd as creds_list_run,
    update_credential_cmd as creds_update_run,
    upsert_credential_cmd as creds_upsert_run,
)
from allta_cli.commands.ilo import ilo_cmd as ilo_run
from allta_cli.commands.git import git_clone
from allta_cli.commands.python import install_python, create_venv
from allta_cli.commands import mc as mc_cmd
from allta_cli.commands import vm as vm_api
from allta_cli.commands import vm_local as vm_local_api
from allta_cli.commands import server as server_api

COMMANDS_NO_AUTH = (
    "login",
    "boxes",
    "releases",
    "mc",
    "local",
    "python",
    "venv",
)

COMMANDS_WITH_AUTH = (
    "logout",
    "git",
    "tokens",
    "ilo",
    "creds",
    "file",
    "ssh",
    "server",
    "vm",
)

COMMAND_SHORTCUTS = {
    "lg": "login",
    "lo": "logout",
    "g": "git",
    "t": "tokens",
    "i": "ilo",
    "cr": "creds",
    "f": "file",
    "bx": "boxes",
    "rel": "releases",
    "py": "python",
    "srv": "server",
    "lc": "local",
}
COMMAND_SHORTCUT_NAMES = tuple(COMMAND_SHORTCUTS.keys())

LOGIN_PASSWORD_ENV_VARS = ("ALLTA_PASSWORD",)
LOGIN_API_TOKEN_ENV_VARS = ("ALLTA_API_TOKEN", "ALLTA_TOKEN")


@dataclass
class LoginCredentials:
    user: str
    secret: str
    auth_mode: str
    secret_source: str


# --- универсальная обёртка для секций ---
def with_section(title: str):
    def deco(fn):
        def wrapper(*args, **kwargs):
            with ui.section(title):
                return fn(*args, **kwargs)
        # click дружит с простыми обёртками, но сохраним метаданные на всякий случай
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def _looks_like_server_query(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.isdigit():
        return True
    # New format: stand12_srv-main (or stand12-srv-main).
    if re.match(r"^(?i:stand)\d+[-_].+", stripped):
        return True
    # Legacy format: 12-srv-main.
    parts = stripped.split("-", 1)
    return len(parts) == 2 and parts[0].isdigit() and bool(parts[1].strip())


def _print_raw_json(payload: object) -> None:
    import json as _json

    ui.echo(_json.dumps(payload, ensure_ascii=False, indent=2))


def _task_id_or_dash(payload: dict) -> str:
    task_id = str(payload.get("task_id") or "").strip()
    return task_id or "-"


class NoUsageCommand(click.Command):
    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_help_text(ctx, formatter)
        self.format_arguments(ctx, formatter)
        self.format_options(ctx, formatter)
        self.format_epilog(ctx, formatter)

    def format_arguments(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows = []
        for param in self.get_params(ctx):
            if not isinstance(param, click.Argument):
                continue
            arg_name = param.make_metavar(ctx)
            if param.nargs == -1:
                description = "Можно указать несколько значений."
            elif param.required:
                description = "Обязательный аргумент."
            else:
                description = "Необязательный аргумент."
            rows.append((arg_name, description))
        if rows:
            with formatter.section("Arguments"):
                formatter.write_dl(rows)


class NoUsageGroup(click.Group):
    command_class = NoUsageCommand

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self.format_help_text(ctx, formatter)
        self.format_arguments(ctx, formatter)
        self.format_options(ctx, formatter)
        self.format_epilog(ctx, formatter)

    def format_arguments(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        return


class SectionedGroup(NoUsageGroup):
    command_sections: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def list_commands(self, ctx):
        existing = super().list_commands(ctx)
        ordered: list[str] = []
        for _, names in self.command_sections:
            for name in names:
                if name in existing and name not in ordered:
                    ordered.append(name)
        remaining = sorted(name for name in existing if name not in ordered)
        return ordered + remaining

    def format_commands(self, ctx, formatter):
        base_get_command = super(SectionedGroup, self).get_command
        commands = {name: base_get_command(ctx, name) for name in self.list_commands(ctx)}
        shown: set[str] = set()

        for title, names in self.command_sections:
            rows = []
            for name in names:
                cmd = commands.get(name)
                if cmd is None or cmd.hidden:
                    continue
                rows.append((name, cmd.get_short_help_str()))
                shown.add(name)
            if rows:
                with formatter.section(title):
                    formatter.write_dl(rows)

        extra_rows = []
        for name in self.list_commands(ctx):
            if name in shown:
                continue
            cmd = commands.get(name)
            if cmd is None or cmd.hidden:
                continue
            extra_rows.append((name, cmd.get_short_help_str()))
        if extra_rows:
            with formatter.section("Другие команды"):
                formatter.write_dl(extra_rows)


# --- Группа с предсказуемым порядком и авто-help ---
class OrderedGroup(NoUsageGroup):
    group_class = NoUsageGroup

    def __init__(self, *a, **kw):
        kw.setdefault("no_args_is_help", True)
        super().__init__(*a, **kw)

    def list_commands(self, ctx):
        order = [*COMMANDS_NO_AUTH, *COMMANDS_WITH_AUTH]
        existing = super().list_commands(ctx)
        ordered = [c for c in order if c in existing]
        remaining = [c for c in existing if c not in order]
        return ordered + remaining + [alias for alias in COMMAND_SHORTCUT_NAMES if alias not in ordered and alias not in remaining]

    def get_command(self, ctx, cmd_name):
        alias_target = COMMAND_SHORTCUTS.get(cmd_name)
        if alias_target:
            return super().get_command(ctx, alias_target)
        return super().get_command(ctx, cmd_name)

    def format_commands(self, ctx, formatter):
        base_get_command = super(OrderedGroup, self).get_command
        commands = {name: base_get_command(ctx, name) for name in self.list_commands(ctx)}

        sections = [
            ("Команды без входа", COMMANDS_NO_AUTH),
            ("Команды после входа", COMMANDS_WITH_AUTH),
        ]
        for title, names in sections:
            rows = []
            for name in names:
                cmd = commands.get(name)
                if cmd is None or cmd.hidden:
                    continue
                rows.append((name, cmd.get_short_help_str()))
            if rows:
                with formatter.section(title):
                    formatter.write_dl(rows)

        shortcut_rows = []
        for alias in COMMAND_SHORTCUT_NAMES:
            cmd = commands.get(alias)
            if cmd is None or cmd.hidden:
                continue
            shortcut_rows.append((alias, f"Шорткат для '{COMMAND_SHORTCUTS[alias]}'"))
        if shortcut_rows:
            with formatter.section("Шорткаты"):
                formatter.write_dl(shortcut_rows)


class VmGroup(SectionedGroup):
    command_sections = (
        ("Информация", ("list", "status")),
        ("Действия с ВМ", ("create", "start", "stop", "astra-update")),
        ("Статус", ("status-set", "status-free")),
        ("Снимки", ("snapshots", "snapshot-create", "snapshot-delete", "snapshot-revert")),
    )


class LocalVmGroup(SectionedGroup):
    command_sections = (
        ("Информация", ("list", "status", "snapshots")),
        ("Действия с ВМ", ("build", "start", "stop", "astra-update")),
        ("Снимки", ("snapshot-create", "snapshot-delete", "snapshot-revert")),
    )


class ServerGroup(SectionedGroup):
    command_sections = (
        ("Информация", ("list", "show")),
        ("Изменение", ("add", "upd", "passwd", "del")),
        ("Пароли снимков", ("password",)),
        ("Действия", ("start", "stop", "reboot", "release", "os-set", "os-refresh", "on", "off")),
    )

    def resolve_command(self, ctx, args):
        if args and not args[0].startswith("-"):
            first = args[0]
            if first not in self.commands and _looks_like_server_query(first):
                cmd = self.get_command(ctx, "show")
                if cmd is not None:
                    return "show", cmd, args
        return super().resolve_command(ctx, args)

    def get_command(self, ctx, cmd_name):
        if cmd_name in {"passwords"}:
            cmd_name = "password"
        return super().get_command(ctx, cmd_name)


class TokensGroup(NoUsageGroup):
    def resolve_command(self, ctx, args):
        if args and not args[0].startswith("-"):
            first = args[0]
            if first not in self.commands:
                cmd = self.get_command(ctx, "get")
                if cmd is not None:
                    return "get", cmd, args
        return super().resolve_command(ctx, args)


class CredsGroup(NoUsageGroup):
    def resolve_command(self, ctx, args):
        if args and not args[0].startswith("-"):
            first = args[0]
            if first not in self.commands:
                cmd = self.get_command(ctx, "get")
                if cmd is not None:
                    return "get", cmd, args
        return super().resolve_command(ctx, args)


def _split_csv_values(values: tuple[str, ...] | list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        for part in str(value).split(","):
            item = part.strip()
            if item:
                result.append(item)
    return result


def _dedupe_keep_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _resolve_vm_names(*, positional: tuple[str, ...], option_values: tuple[str, ...]) -> list[str]:
    names = _dedupe_keep_order([*_split_csv_values(option_values), *[x.strip() for x in positional if x.strip()]])
    if not names:
        raise click.UsageError("Нужно указать хотя бы одну ВМ: позиционно или через --vms.")
    return names


def _resolve_single_vm_name(*, positional: str | None, option_value: str | None) -> str:
    pos = positional.strip() if positional else None
    opt = option_value.strip() if option_value else None
    if pos and opt and pos != opt:
        raise click.UsageError("Имя ВМ указано дважды по-разному: позиционно и через --vm.")
    vm_name = opt or pos
    if not vm_name:
        raise click.UsageError("Нужно указать имя ВМ: позиционно или через --vm.")
    return vm_name


def _first_env_value(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _prompt_secret(prompt_label: str, env_names: tuple[str, ...]) -> str:
    if not sys.stdin.isatty():
        env_names_text = ", ".join(env_names)
        raise click.UsageError(
            f"Секрет не передаётся в аргументах. Установите {env_names_text} или запустите allta login в интерактивном терминале."
        )

    try:
        secret = getpass.getpass(f"{prompt_label}: ")
    except (EOFError, KeyboardInterrupt) as e:
        raise click.ClickException(f"Ввод {prompt_label.lower()} прерван.") from e

    if not secret:
        raise click.UsageError(f"{prompt_label} не должен быть пустым.")
    return secret


def _prompt_text(prompt_label: str) -> str:
    if not sys.stdin.isatty():
        raise click.UsageError(
            f"{prompt_label} не передаётся в аргументах. Запустите allta login в интерактивном терминале."
        )

    try:
        value = input(f"{prompt_label}: ").strip()
    except (EOFError, KeyboardInterrupt) as e:
        raise click.ClickException(f"Ввод {prompt_label.lower()} прерван.") from e

    if not value:
        raise click.UsageError(f"{prompt_label} не должен быть пустым.")
    return value


def _resolve_login_credentials(
    *,
    positionals: list[str],
    user_opt: str | None,
    pass_opt: str | None,
    token_flag: bool,
) -> LoginCredentials:
    if token_flag:
        if len(positionals) > 1:
            raise click.UsageError("Для входа по API token передайте только один позиционный аргумент: USERNAME.")

        pos_user = positionals[0] if positionals else None
        if pos_user and user_opt and pos_user != user_opt:
            raise click.UsageError("Логин указан дважды по-разному (позиционно и через -u/--user).")
        if pass_opt:
            raise click.UsageError("Нельзя одновременно использовать --token и пароль. Уберите пароль из команды.")

        user = user_opt or pos_user or _prompt_text("Логин")

        env_token = _first_env_value(LOGIN_API_TOKEN_ENV_VARS)
        secret = env_token or _prompt_secret("API token", LOGIN_API_TOKEN_ENV_VARS)
        source = "env" if env_token else "prompt"
        return LoginCredentials(user=user, secret=secret, auth_mode="api_token", secret_source=source)

    if len(positionals) > 2:
        raise click.UsageError("Для входа передайте не более двух позиционных аргументов: USERNAME PASSWORD.")

    pos_user = positionals[0] if len(positionals) >= 1 else None
    pos_pass = positionals[1] if len(positionals) >= 2 else None
    if pos_user and user_opt and pos_user != user_opt:
        raise click.UsageError("Логин указан дважды по-разному (позиционно и через -u/--user).")
    if pos_pass and pass_opt and pos_pass != pass_opt:
        raise click.UsageError("Пароль указан дважды по-разному (позиционно и через -p/--password).")

    user = user_opt or pos_user or _prompt_text("Логин")

    env_password = _first_env_value(LOGIN_PASSWORD_ENV_VARS)
    password = pass_opt or pos_pass or env_password or _prompt_secret("Пароль", LOGIN_PASSWORD_ENV_VARS)
    if not password:
        raise click.UsageError("Пароль не должен быть пустым.")

    if pass_opt:
        source = "option"
    elif pos_pass:
        source = "positional"
    elif env_password:
        source = "env"
    else:
        source = "prompt"
    return LoginCredentials(user=user, secret=password, auth_mode="password", secret_source=source)


def _ensure_authenticated_or_exit() -> None:
    try:
        auth_utils.load_token(verbose=False)
    except auth_utils.NotAuthenticatedError:
        ui.err("Требуется авторизация. Выполните вход: allta login <login>")
        raise SystemExit(1)
    except auth_utils.TokenExpiredError as e:
        ui.err(f"{e} Выполните вход заново: allta login <login>")
        raise SystemExit(1)
    except auth_utils.AuthError as e:
        ui.err(f"Ошибка авторизации: {e}")
        raise SystemExit(1)


def run_login_shortcut(
    *,
    user: str,
    secret: str,
    auth_mode: str = "password",
    secret_source: str = "prompt",
    show_section: bool = True,
    git: bool = False,
    tokens: bool = False,
    token_keys: tuple[str, ...] = (),
    ilo: bool = False,
    ilo_stand: str | None = None,
    files: tuple[str, ...] = (),
    boxes_flag: bool = False,
    releases_flag: bool = False,
) -> int:
    def _do_login() -> int:
        try:
            if auth_mode == "password":
                if secret_source in {"positional", "option"}:
                    ui.warn(
                        "Передача пароля в аргументах командной строки небезопасна, устаревает и будет удалена в будущих версиях. "
                        "Используйте ALLTA_PASSWORD, интерактивный ввод или вход по API token через --token и ALLTA_API_TOKEN."
                    )
                ui.http("POST http://allta.devos.astralinux.ru:21500/api/auth/login")
                auth_utils.login(login=user, password=secret, verbose=False)
            else:
                ui.http("GET http://allta.devos.astralinux.ru:21500/api/auth/verify")
                auth_utils.login_with_api_token(login=user, api_token=secret, verbose=False)
            ui.ok("Вход выполнен.")
        except auth_utils.AuthError as e:
            ui.err(f"Ошибка входа: {e}")
            return 1
        return 0

    if show_section:
        with ui.section("LOGIN"):
            code = _do_login()
    else:
        code = _do_login()
    if code != 0:
        return code

    _do_post_login_actions(
        git=git,
        tokens=tokens,
        token_keys=token_keys,
        ilo=ilo,
        ilo_stand=ilo_stand,
        files=files,
        boxes_flag=boxes_flag,
        releases_flag=releases_flag,
    )
    return 0


def run_login_shortcut_argv(argv: list[str]) -> int:
    login_flag = False
    token_flag = False
    user_opt: str | None = None
    pass_opt: str | None = None
    git = False
    tokens = False
    token_keys: list[str] = []
    ilo = False
    ilo_stand: str | None = None
    files: list[str] = []
    boxes_flag = False
    releases_flag = False
    positionals: list[str] = []

    idx = 0
    while idx < len(argv):
        arg = argv[idx]

        if arg in ("-l", "--login"):
            login_flag = True
        elif arg in ("-u", "--user", "--username", "--usermane"):
            idx += 1
            if idx >= len(argv):
                raise click.UsageError("Опция -u/--user требует значение USERNAME.")
            user_opt = argv[idx]
        elif arg.startswith("--user=") or arg.startswith("--username=") or arg.startswith("--usermane="):
            user_opt = arg.split("=", 1)[1]
        elif arg == "--token":
            token_flag = True
        elif arg.startswith("--token="):
            raise click.UsageError("Опция --token не принимает значение. Передайте username отдельно и токен через env или интерактивно.")
        elif arg in ("-p", "--password"):
            idx += 1
            if idx >= len(argv):
                raise click.UsageError("Опция -p/--password требует значение PASSWORD.")
            pass_opt = argv[idx]
        elif arg.startswith("--password="):
            pass_opt = arg.split("=", 1)[1]
        elif arg in ("-g", "--git"):
            git = True
        elif arg in ("-t", "--tokens"):
            tokens = True
        elif arg in ("-k", "--token-key"):
            idx += 1
            if idx >= len(argv):
                raise click.UsageError("Опция -k/--token-key требует значение KEY.")
            token_keys.append(argv[idx])
        elif arg.startswith("--token-key="):
            token_keys.append(arg.split("=", 1)[1])
        elif arg in ("-i", "--ilo"):
            ilo = True
        elif arg in ("-s", "--stand"):
            idx += 1
            if idx >= len(argv):
                raise click.UsageError("Опция -s/--stand требует значение STAND.")
            ilo_stand = argv[idx]
        elif arg.startswith("--stand="):
            ilo_stand = arg.split("=", 1)[1]
        elif arg in ("-f", "--file"):
            idx += 1
            if idx >= len(argv):
                raise click.UsageError("Опция -f/--file требует значение FILENAME.")
            files.append(argv[idx])
        elif arg.startswith("--file="):
            files.append(arg.split("=", 1)[1])
        elif arg in ("-b", "--boxes"):
            boxes_flag = True
        elif arg in ("-r", "--releases"):
            releases_flag = True
        elif arg == "--":
            positionals.extend(argv[idx + 1:])
            break
        elif arg.startswith("-"):
            raise click.UsageError(f"Неизвестная опция для shortcut login: {arg}")
        else:
            positionals.append(arg)
        idx += 1

    if not login_flag:
        raise click.UsageError("Shortcut login требует флаг -l/--login.")

    creds = _resolve_login_credentials(
        positionals=positionals,
        user_opt=user_opt,
        pass_opt=pass_opt,
        token_flag=token_flag,
    )
    return run_login_shortcut(
        user=creds.user,
        secret=creds.secret,
        auth_mode=creds.auth_mode,
        secret_source=creds.secret_source,
        git=git,
        tokens=tokens,
        token_keys=tuple(token_keys),
        ilo=ilo,
        ilo_stand=ilo_stand,
        files=tuple(files),
        boxes_flag=boxes_flag,
        releases_flag=releases_flag,
    )


def _do_post_login_actions(
    *,
    git: bool = False,
    tokens: bool = False,
    token_keys: tuple[str, ...] = (),
    ilo: bool = False,
    ilo_stand: str | None = None,
    files: tuple[str, ...] = (),
    boxes_flag: bool = False,
    releases_flag: bool = False,
):
    """Пост-действия сразу после входа."""
    # ВНИМАНИЕ: сами called-команды уже печатают свои разделители.
    if git:
        ui.step("Выполняю git clone…")
        rc = git_clone()
        if rc != 0:
            ui.err(f"git clone завершился с кодом {rc}")
            sys.exit(rc)

    if tokens:
        ui.step("Получаю tokens.json…")
        code = tokens_run(None)
        if code != 0:
            sys.exit(code)

    for token_key in token_keys:
        ui.step(f"Получаю токен '{token_key}'…")
        code = tokens_run(token_key)
        if code != 0:
            sys.exit(code)

    if ilo or ilo_stand:
        target = ilo_stand or None
        if target:
            ui.step(f"Получаю iLO для стенда '{target}'…")
        else:
            ui.step("Получаю iLO credentials…")
        code = ilo_run(stand_query=target, raw=False)
        if code != 0:
            sys.exit(code)

    for filename in files:
        ui.step(f"Получаю файл '{filename}'…")
        code = files_run(filename)
        if code != 0:
            sys.exit(code)

    if boxes_flag:
        ui.step("Получаю test-box-config.json…")
        code = boxes_cmd()
        if code != 0:
            sys.exit(code)

    if releases_flag:
        ui.step("Получаю releases.json…")
        code = releases_cmd()
        if code != 0:
            sys.exit(code)


CONTEXT_SETTINGS = dict(
    help_option_names=["-h", "--help"],
    max_content_width=100,
)

@click.group(
    cls=OrderedGroup,
    context_settings=CONTEXT_SETTINGS,
    invoke_without_command=True,
    help=(
        "allta — CLI для вспомогательных действий (авторизация, файлы, токены, git, Python).\n\n"
        "Команды без входа:\n"
        "  allta boxes | allta -b\n"
        "  allta releases | allta -r\n"
        "  allta login [USERNAME] [PASSWORD]\n"
        "  allta local vm ...\n\n"
        "Команды после входа:\n"
        "  allta ilo [STAND]\n"
        "  allta creds [SERVICE_NAME]\n"
        "  allta tokens [KEY]\n"
        "  allta file FILENAME\n\n"
        "Шорткаты команд:\n"
        "  lg -> login, lo -> logout, g -> git, t -> tokens\n"
        "  i -> ilo, cr -> creds, f -> file, bx -> boxes, rel -> releases, py -> python\n\n"
        "Шорткат входа:\n"
        "  allta -l [OPTIONS] USERNAME [PASSWORD] [OPTIONS]\n"
    ),
    epilog=(
        "Примеры:\n"
        "  allta login\n"
        "  allta login user secret -t\n"
        "  allta -l -t user secret\n"
        "  ALLTA_PASSWORD=secret allta login user -k git_token -i\n"
        "  ALLTA_API_TOKEN=jwt allta login user --token\n"
        "  allta -l user --token -s 12 -f releases.json\n"
        "  allta creds\n"
        "  allta creds -s nexus\n"
        "  allta creds -a nexus -u admin -p secret\n"
        "  allta creds -up nexus -p new-secret\n"
        "  allta vm start vm1 vm2\n"
        "  allta vm stop --vms vm1,vm2 vm3\n"
        "  allta file releases.json\n"
        "  allta i 12\n"
        "  allta g\n"
    ),
)
@click.version_option(version="0.1.0", prog_name="allta")
@click.option("-l", "--login", "login_flag", is_flag=True,
              help="Войти без явной сабкоманды (короткая форма).")
@click.option("-u", "--user", "--username", "--usermane", "top_user",
              metavar="USERNAME", default=None, show_default=False,
              help="Имя пользователя для -l.")
@click.option("-p", "--password", "top_password",
              metavar="PASSWORD", default=None, show_default=False,
              help="Пароль для -l. Небезопасно: будет удалено в будущих версиях.")
@click.option("--token", "top_token_flag", is_flag=True,
              help="Войти по API token вместо пароля. Токен берётся из ALLTA_API_TOKEN/ALLTA_TOKEN или запрашивается интерактивно.")
@click.option("-g", "--git", is_flag=True, help="После входа: выполнить git clone.")
@click.option("-t", "--tokens", is_flag=True, help="После входа: получить tokens.json.")
@click.option("-k", "--token-key", "token_keys", multiple=True, metavar="KEY",
              help="После входа: получить конкретный ключ из tokens.json. Можно указать несколько раз.")
@click.option("-i", "--ilo", "ilo_flag", is_flag=True,
              help="После входа: показать все iLO credentials.")
@click.option("-s", "--stand", "ilo_stand", metavar="STAND", default=None, show_default=False,
              help="После входа: показать iLO только для указанного стенда (например, 12 или stand12).")
@click.option("-f", "--file", "login_files", multiple=True, metavar="FILENAME",
              help="После входа: скачать JSON-файл из config-API. Можно указать несколько раз.")
@click.option("-b", "--boxes", "boxes_flag", is_flag=True,
              help="Показать имена боксов (без входа).")
@click.option("-r", "--releases", "releases_flag", is_flag=True,
              help="Показать версии releases (без входа).")
@click.pass_context
def cli(ctx: click.Context,
        login_flag: bool,
        top_user: str | None,
        top_password: str | None,
        top_token_flag: bool,
        git: bool, tokens: bool, token_keys: tuple[str, ...], ilo_flag: bool, ilo_stand: str | None,
        login_files: tuple[str, ...], boxes_flag: bool, releases_flag: bool):
    # если вызвали сабкоманду — отдаём управление ей
    if ctx.invoked_subcommand is not None:
        return

    # без сабкоманды: если явно попросили -b/-r — выполним их (обернём в секции здесь)
    if not login_flag:
        if boxes_flag:
            with ui.section("BOXES"):
                ui.step("Получаю test-box-config.json…")
                code = boxes_cmd()
                sys.exit(code)
        if releases_flag:
            with ui.section("RELEASES"):
                ui.step("Получаю releases.json…")
                code = releases_cmd()
                sys.exit(code)
        return  # no_args_is_help=True сам покажет help

    # режим шортката входа (-l)
    creds = _resolve_login_credentials(
        positionals=[],
        user_opt=top_user,
        pass_opt=top_password,
        token_flag=top_token_flag,
    )
    ctx.exit(run_login_shortcut(
        user=creds.user,
        secret=creds.secret,
        auth_mode=creds.auth_mode,
        secret_source=creds.secret_source,
        show_section=True,
        git=git,
        tokens=tokens,
        token_keys=token_keys,
        ilo=ilo_flag,
        ilo_stand=ilo_stand,
        files=login_files,
        boxes_flag=boxes_flag,
        releases_flag=releases_flag,
    ))


# --- команды ---

@cli.command("releases", short_help="Показать версии releases.json.",
             help="Показать доступные версии из releases.json.")
@with_section("RELEASES")
def releases_cli():
    ui.step("Получаю releases.json…")
    code = releases_cmd()
    sys.exit(code)

@cli.command("boxes", short_help="Показать имена боксов.",
             help="Показать имена боксов (libvirt_box) из test-box-config.json.")
@with_section("BOXES")
def boxes_cli():
    ui.step("Получаю test-box-config.json…")
    code = boxes_cmd()
    sys.exit(code)

@cli.command("git", short_help="Клонировать репозиторий.",
             help="Клонировать фиксированный репозиторий (через токен из tokens.json).")
@with_section("GIT")
def git_cmd():
    rc = git_clone()
    if rc != 0:
        sys.exit(rc)

@cli.group(
    "tokens",
    cls=TokensGroup,
    short_help="Токены config-API.",
    help="Управление токенами: list/get/set/upd/del. Без подкоманды — показать tokens.json.",
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
)
@click.pass_context
def tokens_group(ctx: click.Context):
    if ctx.invoked_subcommand is not None:
        return
    with ui.section("TOKENS"):
        code = tokens_run(None)
    ctx.exit(code)


@tokens_group.command("list", short_help="Показать список токенов (details).")
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
def tokens_list_cli(raw: bool):
    with ui.section("TOKENS"):
        code = tokens_list_run(raw=raw)
    sys.exit(code)


@tokens_group.command("get", short_help="Показать один токен по ключу.")
@click.argument("token_key", metavar="TOKEN_KEY")
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
def tokens_get_cli(token_key: str, raw: bool):
    with ui.section("TOKENS"):
        code = tokens_get_run(token_key, raw=raw)
    sys.exit(code)


@tokens_group.command("set", short_help="Создать или перезаписать токен.")
@click.argument("token_key", metavar="TOKEN_KEY")
@click.option("--token", "token_value", default=None, show_default=False, help="Значение токена.")
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
def tokens_set_cli(token_key: str, token_value: str | None, raw: bool):
    value = token_value or _prompt_hidden_password("Введите значение токена")
    with ui.section("TOKENS"):
        code = tokens_upsert_run(token_key, value, raw=raw)
    sys.exit(code)


@tokens_group.command("upd", short_help="Обновить существующий токен.")
@click.argument("token_key", metavar="TOKEN_KEY")
@click.option("--token", "token_value", default=None, show_default=False, help="Новое значение токена.")
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
def tokens_upd_cli(token_key: str, token_value: str | None, raw: bool):
    value = token_value or _prompt_hidden_password("Введите новое значение токена")
    with ui.section("TOKENS"):
        code = tokens_update_run(token_key, value, raw=raw)
    sys.exit(code)


@tokens_group.command("del", short_help="Удалить токен.")
@click.argument("token_key", metavar="TOKEN_KEY")
@click.option("--yes", is_flag=True, help="Удалить без подтверждения.")
def tokens_del_cli(token_key: str, yes: bool):
    if not yes:
        click.confirm(f"Удалить токен '{token_key}'?", abort=True)
    with ui.section("TOKENS"):
        code = tokens_delete_run(token_key)
    sys.exit(code)


@cli.command("ilo", short_help="Получить iLO-креды.",
             help="Получить iLO-креды из /server/ilo (динамически из allta_server_api).")
@click.argument("stand_num", required=False, metavar="[STAND]")
@click.option("--raw", is_flag=True, help="Показать полный JSON без попытки разбора.")
@with_section("ILO")
def ilo_cli(stand_num: str | None, raw: bool):
    code = ilo_run(stand_query=stand_num, raw=raw)
    sys.exit(code)


@cli.group(
    "creds",
    cls=CredsGroup,
    short_help="Сервисные логины и пароли.",
    help="Управление сервисными кредами: list/get/set/upd/del. Без подкоманды — список сервисов.",
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
)
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
@click.pass_context
def creds_group(ctx: click.Context, raw: bool):
    if ctx.invoked_subcommand is not None:
        return
    with ui.section("CREDS"):
        code = creds_list_run(raw=raw)
    ctx.exit(code)


@creds_group.command("list", short_help="Показать все сервисные креды.")
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
def creds_list_cli(raw: bool):
    with ui.section("CREDS"):
        code = creds_list_run(raw=raw)
    sys.exit(code)


@creds_group.command("get", short_help="Показать креды сервиса.")
@click.argument("service_name", metavar="SERVICE_NAME")
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
def creds_get_cli(service_name: str, raw: bool):
    with ui.section("CREDS"):
        code = creds_get_run(service_name, raw=raw)
    sys.exit(code)


@creds_group.command("set", short_help="Создать или перезаписать креды сервиса.")
@click.argument("service_name", metavar="SERVICE_NAME")
@click.option("-u", "--user", "--username", "username", required=True, metavar="USERNAME", help="Логин сервиса.")
@click.option("-p", "--password", "password", default=None, show_default=False, help="Пароль сервиса.")
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
def creds_set_cli(service_name: str, username: str, password: str | None, raw: bool):
    pwd = password or _prompt_hidden_password("Введите пароль сервиса")
    with ui.section("CREDS"):
        code = creds_upsert_run(service_name, username, pwd, raw=raw)
    sys.exit(code)


@creds_group.command("upd", short_help="Обновить логин и/или пароль сервиса.")
@click.argument("service_name", metavar="SERVICE_NAME")
@click.option("-u", "--user", "--username", "username", default=None, show_default=False, metavar="USERNAME",
              help="Новый логин сервиса.")
@click.option("-p", "--password", "password", default=None, show_default=False, help="Новый пароль сервиса.")
@click.option("--raw", is_flag=True, help="Показать полный JSON.")
def creds_upd_cli(service_name: str, username: str | None, password: str | None, raw: bool):
    if username is None and password is None:
        raise click.UsageError("Для upd укажите хотя бы одно поле: -u/--user или -p/--password.")
    with ui.section("CREDS"):
        code = creds_update_run(service_name, username=username, password=password, raw=raw)
    sys.exit(code)


@creds_group.command("del", short_help="Удалить креды сервиса.")
@click.argument("service_name", metavar="SERVICE_NAME")
@click.option("--yes", is_flag=True, help="Удалить без подтверждения.")
def creds_del_cli(service_name: str, yes: bool):
    if not yes:
        click.confirm(f"Удалить креды сервиса '{service_name}'?", abort=True)
    with ui.section("CREDS"):
        code = creds_delete_run(service_name)
    sys.exit(code)


@cli.command("file", short_help="Скачать JSON-файл из config-API.",
             help="Скачать JSON-файл из config-API (например, releases.json).")
@click.argument("filename", required=True, metavar="FILENAME")
@with_section("FILES")
def file_cli(filename: str):
    code = files_run(filename)
    sys.exit(code)

@cli.command("login", short_help="Авторизоваться.",
             help="Авторизоваться по паролю или по API token.")
@click.argument("username", required=False, metavar="[USERNAME]")
@click.argument("password", required=False, metavar="[PASSWORD]")
@click.option("-u", "--user", "--username", "--usermane", "user_opt",
              metavar="USERNAME", default=None, show_default=False,
              help="Логин (если не указан позиционно).")
@click.option("-p", "--password", "pass_opt",
              metavar="PASSWORD", default=None, show_default=False,
              help="Пароль (если не указан позиционно). Небезопасно: будет удалено в будущих версиях.")
@click.option("--token", "token_flag", is_flag=True,
              help="Войти по API token вместо пароля. Токен берётся из ALLTA_API_TOKEN/ALLTA_TOKEN или запрашивается интерактивно.")
@click.option("-g", "--git", is_flag=True, help="После входа: git clone.")
@click.option("-t", "--tokens", is_flag=True, help="После входа: получить tokens.json.")
@click.option("-k", "--token-key", "token_keys", multiple=True, metavar="KEY",
              help="После входа: получить конкретный ключ из tokens.json. Можно указать несколько раз.")
@click.option("-i", "--ilo", "ilo_flag", is_flag=True, help="После входа: показать все iLO credentials.")
@click.option("-s", "--stand", "ilo_stand", metavar="STAND", default=None, show_default=False,
              help="После входа: показать iLO только для указанного стенда.")
@click.option("-f", "--file", "login_files", multiple=True, metavar="FILENAME",
              help="После входа: скачать JSON-файл из config-API. Можно указать несколько раз.")
@click.option("-b", "--boxes", "boxes_flag", is_flag=True, help="После входа: показать boxes.")
@click.option("-r", "--releases", "releases_flag", is_flag=True, help="После входа: показать releases.")
@with_section("LOGIN")
def login_cmd(username: str | None, password: str | None,
              user_opt: str | None, pass_opt: str | None, token_flag: bool,
              git: bool, tokens: bool, token_keys: tuple[str, ...], ilo_flag: bool, ilo_stand: str | None,
              login_files: tuple[str, ...], boxes_flag: bool, releases_flag: bool):
    creds = _resolve_login_credentials(
        positionals=[x for x in (username, password) if x is not None],
        user_opt=user_opt,
        pass_opt=pass_opt,
        token_flag=token_flag,
    )
    sys.exit(run_login_shortcut(
        user=creds.user,
        secret=creds.secret,
        auth_mode=creds.auth_mode,
        secret_source=creds.secret_source,
        show_section=False,
        git=git,
        tokens=tokens,
        token_keys=token_keys,
        ilo=ilo_flag,
        ilo_stand=ilo_stand,
        files=login_files,
        boxes_flag=boxes_flag,
        releases_flag=releases_flag,
    ))

@cli.command("logout", short_help="Выйти (revoke).",
             help="Отозвать текущий токен на сервере и удалить локальную сессию.")
@with_section("LOGOUT")
def logout_cmd():
    try:
        auth_utils.logout(verbose=True)
    except auth_utils.AuthError as e:
        ui.err(f"Ошибка logout: {e}")
        sys.exit(1)

@cli.command("python", short_help="Установить Python и venv.",
             help="Установить Python в домашний каталог и создать venv по умолчанию.")
@click.option("--venv", "with_shell", is_flag=True, default=False, show_default=True,
              help="После установки сразу открыть shell с активированным venv.")
@with_section("PYTHON")
def python_install_cmd(with_shell: bool):
    rc = install_python(activate_shell=with_shell)
    sys.exit(rc)

@cli.command("venv", short_help="Создать venv.",
             help="Создать виртуальное окружение (по умолчанию ./venv).")
@click.option("--path", "venv_path", type=click.Path(file_okay=False, dir_okay=True),
              metavar="DIR", default=None, show_default=False,
              help="Каталог для venv (должен существовать). По умолчанию: ./venv.")
@with_section("VENV")
def venv_cmd(venv_path: str | None):
    rc = create_venv(venv_path, enter_shell=True)
    sys.exit(rc)

@cli.group("mc", short_help="Открыть MC на преднастроенных FTP.", context_settings=CONTEXT_SETTINGS)
def mc_group():
    """Открыть Midnight Commander на преднастроенных FTP-хостах."""


# Подключаем группу vm напрямую, чтобы корректно работал help
# Создаём click-группу здесь и используем функции из commands/vm.py
@cli.group("vm", cls=VmGroup, short_help="Управление виртуальными машинами.", context_settings=CONTEXT_SETTINGS)
def vm_group():
    pass


@cli.group("local", short_help="Локальные команды без авторизации.", context_settings=CONTEXT_SETTINGS)
def local_group():
    pass


@local_group.group(
    "vm",
    cls=LocalVmGroup,
    short_help="Локальные VM через allta_lib.",
    help=(
        "Локальные VM через allta_lib без API.\n"
        "Состояние хранится в ~/.config/allta/local_vm/:\n"
        "- vms.json: inventory локальных ВМ\n"
        "- snapshots.json: inventory snapshot'ов\n"
        "- prepare.json: состояние этапа prepare\n"
        "- provider_vms_dates.json: сырые данные провайдера\n\n"
        "ВМ поднимаются в системном libvirt пользователя root.\n"
        "Команды virsh запускайте через sudo "
        "(например: sudo virsh -c qemu:///system list --all)."
    ),
    context_settings=CONTEXT_SETTINGS,
)
def local_vm_group():
    pass


@cli.group("server", cls=ServerGroup, short_help="Управление физическими серверами.", context_settings=CONTEXT_SETTINGS)
def server_group():
    _ensure_authenticated_or_exit()


@server_group.group("password", short_help="CRUD паролей для снимков/ОС.")
def server_snapshot_password_group():
    pass


def _server_table_rows(servers: list[dict]) -> list[list[object]]:
    rows: list[list[object]] = []
    for server in sorted(servers, key=server_api.server_sort_key):
        name = str(server.get("name") or "").strip()
        rows.append([
            server.get("id", ""),
            server_api.stand_number_text(name),
            name,
            server.get("ip_address", ""),
            "yes" if server.get("virtualization") else "no",
            server.get("status", ""),
        ])
    return rows


def _server_details_rows(server: dict) -> list[list[object]]:
    cpu_total = server.get("cpu_total", "")
    cpu_cores = server.get("cpu_cores_count")
    cpu_threads = server.get("cpu_threads")
    return [
        ["ID", server.get("id", "")],
        ["Stand", server_api.stand_number_text(str(server.get("name") or ""))],
        ["Name", server.get("name", "")],
        ["IP", server.get("ip_address", "")],
        ["SSH Port", server.get("ssh_port", "")],
        ["Driver", server.get("driver_type", "")],
        ["Admin Panel IP", server.get("admin_panel_ip", "")],
        ["Admin Panel User", server.get("admin_panel_user", "")],
        ["Admin Panel Password", server.get("admin_panel_pass", "")],
        ["Grade", server.get("grade", "")],
        ["CPU Model", server.get("cpu_model", "")],
        ["CPU", cpu_total],
        ["CPU Cores", cpu_cores if cpu_cores is not None else cpu_total],
        ["CPU Threads", cpu_threads if cpu_threads is not None else (cpu_cores if cpu_cores is not None else cpu_total)],
        ["RAM", server.get("ram_total", "")],
        ["Storage", server.get("storage", "")],
        ["GPU", server.get("gpu", "")],
        ["Virtualization", "yes" if server.get("virtualization") else "no"],
        ["Physical IF", server.get("phy_if", "")],
        ["Status", server.get("status", "")],
        ["OS Version", server.get("os_version", "") or server.get("os_version_name", "")],
    ]


def _server_identity_rows(server: dict) -> list[list[object]]:
    return [
        ["Name", server.get("name", "")],
        ["Stand", server_api.stand_number_text(str(server.get("name") or ""))],
        ["ID", server.get("id", "")],
        ["Status", server.get("status", "")],
    ]


def _server_network_rows(server: dict) -> list[list[object]]:
    return [
        ["Server IP", server.get("ip_address", "")],
        ["SSH Port", server.get("ssh_port", "")],
        ["Physical IF", server.get("phy_if", "")],
        ["Virtualization", "yes" if server.get("virtualization") else "no"],
    ]


def _server_hardware_rows(server: dict) -> list[list[object]]:
    cpu_total = server.get("cpu_total", "")
    cpu_cores = server.get("cpu_cores_count")
    cpu_threads = server.get("cpu_threads")
    return [
        ["Grade", server.get("grade", "")],
        ["CPU Model", server.get("cpu_model", "")],
        ["CPU", cpu_total],
        ["CPU Cores", cpu_cores if cpu_cores is not None else cpu_total],
        ["CPU Threads", cpu_threads if cpu_threads is not None else (cpu_cores if cpu_cores is not None else cpu_total)],
        ["RAM", server.get("ram_total", "")],
        ["Storage", server.get("storage", "")],
        ["GPU", server.get("gpu", "")],
        ["OS Version", server.get("os_version", "") or server.get("os_version_name", "")],
        ["Driver", server.get("driver_type", "")],
    ]


def _server_access_rows(server: dict) -> list[list[object]]:
    return [
        ["IPMI/iLO IP", server.get("admin_panel_ip", "")],
        ["IPMI/iLO User", server.get("admin_panel_user", "")],
        ["IPMI/iLO Password", server.get("admin_panel_pass", "")],
    ]


def _render_server_view(server: dict) -> None:
    title = str(server.get("name") or f"id={server.get('id', '?')}")
    with ui.section(f"SERVER {title}"):
        ui.step("Идентификация")
        ui.table(headers=["Field", "Value"], rows=_server_identity_rows(server))
        ui.echo("")
        ui.step("Сеть")
        ui.table(headers=["Field", "Value"], rows=_server_network_rows(server))
        ui.echo("")
        ui.step("Железо")
        ui.table(headers=["Field", "Value"], rows=_server_hardware_rows(server))
        ui.echo("")
        ui.step("Доступ")
        ui.table(headers=["Field", "Value"], rows=_server_access_rows(server))


def _prompt_hidden_password(prompt: str) -> str:
    return click.prompt(prompt, hide_input=True, confirmation_prompt=True, type=str)


def _format_cli_dt(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = text.replace("T", " ")
    if "." in text:
        text = text.split(".", 1)[0]
    return text.rstrip("Z")


def _snapshot_password_rows(items: list[server_api.SnapshotPasswordEntry]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in sorted(items, key=lambda x: x["os_version_name"].lower()):
        rows.append(
            [
                item["os_version_name"],
                item["ssh_username"],
                item["password"],
                item["updated_by"] or "",
                _format_cli_dt(item["updated_at"]),
            ]
        )
    return rows


def _build_server_create_payload(
    *,
    name: str,
    ip_address: str,
    grade: str | None,
    cpu_model: str | None,
    cpu_total: int,
    cpu_cores_count: int | None,
    cpu_threads: int | None,
    ram_total: int,
    storage: str | None,
    gpu: str | None,
    phy_if: str,
    virtualization: bool,
    ssh_port: int,
    driver_type: str,
    admin_panel_ip: str,
    admin_panel_user: str,
    admin_panel_pass: str | None,
    os_version_id: int | None,
) -> dict[str, object]:
    return {
        "name": name,
        "ip_address": ip_address,
        "grade": grade,
        "cpu_model": cpu_model,
        "cpu_total": cpu_total,
        "cpu_cores_count": cpu_cores_count,
        "cpu_threads": cpu_threads,
        "ram_total": ram_total,
        "storage": storage,
        "gpu": gpu,
        "phy_if": phy_if,
        "virtualization": virtualization,
        "ssh_port": ssh_port,
        "driver_type": driver_type,
        "admin_panel_ip": admin_panel_ip,
        "admin_panel_user": admin_panel_user,
        "admin_panel_pass": admin_panel_pass or _prompt_hidden_password("Введите пароль IPMI/iLO"),
        "os_version_id": os_version_id,
    }


def _build_server_update_payload(
    *,
    name: str | None,
    ip_address: str | None,
    grade: str | None,
    cpu_model: str | None,
    cpu_total: int | None,
    cpu_cores_count: int | None,
    cpu_threads: int | None,
    ram_total: int | None,
    storage: str | None,
    gpu: str | None,
    phy_if: str | None,
    virtualization: bool | None,
    ssh_port: int | None,
    driver_type: str | None,
    admin_panel_ip: str | None,
    admin_panel_user: str | None,
    status: str | None,
    os_version_id: int | None,
) -> dict[str, object]:
    payload = {
        "name": name,
        "ip_address": ip_address,
        "grade": grade,
        "cpu_model": cpu_model,
        "cpu_total": cpu_total,
        "cpu_cores_count": cpu_cores_count,
        "cpu_threads": cpu_threads,
        "ram_total": ram_total,
        "storage": storage,
        "gpu": gpu,
        "phy_if": phy_if,
        "virtualization": virtualization,
        "ssh_port": ssh_port,
        "driver_type": driver_type,
        "admin_panel_ip": admin_panel_ip,
        "admin_panel_user": admin_panel_user,
        "status": status,
        "os_version_id": os_version_id,
    }
    return {key: value for key, value in payload.items() if value is not None}


@server_group.command("list", short_help="Показать все серверы.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_list_cli(raw_output: bool):
    try:
        servers = server_api.list_servers(enrich=not raw_output)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)

    if raw_output:
        _print_raw_json(servers)
        return

    if not servers:
        ui.echo("Серверы не найдены.")
        return

    ui.table(
        headers=["ID", "Stand", "Name", "IP", "Virt", "Status"],
        rows=_server_table_rows(servers),
    )


@server_group.command("show", short_help="Показать один сервер по имени или номеру стенда.")
@click.argument("server_query", metavar="SERVER")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_show_cli(server_query: str, raw_output: bool):
    try:
        server = server_api.resolve_server(server_query, enrich=not raw_output)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)

    if raw_output:
        _print_raw_json(server)
        return

    _render_server_view(server)


@server_group.command("add", short_help="Добавить сервер.")
@click.option("--name", required=True, help="Имя сервера в формате stand<number>_<server_name>.")
@click.option("--ip", "ip_address", required=True, help="IP сервера.")
@click.option("--grade", default=None, show_default=False, help="Класс/грейд сервера.")
@click.option("--cpu-model", default=None, show_default=False, help="Модель CPU.")
@click.option("--cpu", "cpu_total", required=True, type=int, help="Количество CPU.")
@click.option("--cpu-cores", "cpu_cores_count", default=None, show_default=False, type=int, help="Количество физических CPU ядер.")
@click.option("--cpu-threads", "cpu_threads", default=None, show_default=False, type=int, help="Количество CPU потоков.")
@click.option("--ram", "ram_total", required=True, type=int, help="Количество RAM.")
@click.option("--storage", default=None, show_default=False, help="Описание дисковой подсистемы.")
@click.option("--gpu", default=None, show_default=False, help="Описание GPU.")
@click.option("--phy-if", default="eth0", show_default=True, help="Физический интерфейс.")
@click.option("--virt/--no-virt", "virtualization", default=False, show_default=True, help="Флаг виртуализации.")
@click.option("--ssh-port", default=22, show_default=True, type=int, help="SSH порт.")
@click.option("--driver-type", type=click.Choice(["ilo", "idrac"]), default="ilo", show_default=True, help="Тип BMC.")
@click.option("--ipmi-ip", "admin_panel_ip", required=True, help="IP IPMI/iLO.")
@click.option("--ipmi-user", "admin_panel_user", required=True, help="Пользователь IPMI/iLO.")
@click.option("--ipmi-password", "admin_panel_pass", default=None, show_default=False, help="Пароль IPMI/iLO.")
@click.option("--os-version-id", default=None, show_default=False, type=int, help="ID версии ОС.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_add_cli(
    name: str,
    ip_address: str,
    grade: str | None,
    cpu_model: str | None,
    cpu_total: int,
    cpu_cores_count: int | None,
    cpu_threads: int | None,
    ram_total: int,
    storage: str | None,
    gpu: str | None,
    phy_if: str,
    virtualization: bool,
    ssh_port: int,
    driver_type: str,
    admin_panel_ip: str,
    admin_panel_user: str,
    admin_panel_pass: str | None,
    os_version_id: int | None,
    raw_output: bool,
):
    try:
        server = server_api.create_server(
            _build_server_create_payload(
                name=name,
                ip_address=ip_address,
                grade=grade,
                cpu_model=cpu_model,
                cpu_total=cpu_total,
                cpu_cores_count=cpu_cores_count,
                cpu_threads=cpu_threads,
                ram_total=ram_total,
                storage=storage,
                gpu=gpu,
                phy_if=phy_if,
                virtualization=virtualization,
                ssh_port=ssh_port,
                driver_type=driver_type,
                admin_panel_ip=admin_panel_ip,
                admin_panel_user=admin_panel_user,
                admin_panel_pass=admin_panel_pass,
                os_version_id=os_version_id,
            )
        )
        if raw_output:
            _print_raw_json(server)
            return
        ui.ok(f"Сервер '{server.get('name')}' создан.")
        _render_server_view(server)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@server_group.command("upd", short_help="Обновить сервер.")
@click.argument("server_query", required=False, metavar="[SERVER]")
@click.option("--all", "update_all", is_flag=True, help="Обновить указанные поля сразу на всех серверах.")
@click.option("--name", default=None, show_default=False, help="Новое имя.")
@click.option("--ip", "ip_address", default=None, show_default=False, help="Новый IP.")
@click.option("--grade", default=None, show_default=False, help="Новый класс/грейд.")
@click.option("--cpu-model", default=None, show_default=False, help="Новая модель CPU.")
@click.option("--cpu", "cpu_total", default=None, show_default=False, type=int, help="Новое количество CPU.")
@click.option("--cpu-cores", "cpu_cores_count", default=None, show_default=False, type=int, help="Новое количество физических CPU ядер.")
@click.option("--cpu-threads", "cpu_threads", default=None, show_default=False, type=int, help="Новое количество CPU потоков.")
@click.option("--ram", "ram_total", default=None, show_default=False, type=int, help="Новое количество RAM.")
@click.option("--storage", default=None, show_default=False, help="Новая дисковая подсистема.")
@click.option("--gpu", default=None, show_default=False, help="Новый GPU.")
@click.option("--phy-if", default=None, show_default=False, help="Новый физический интерфейс.")
@click.option("--virt", "virtualization", flag_value=True, default=None, help="Включить виртуализацию.")
@click.option("--no-virt", "virtualization", flag_value=False, help="Выключить виртуализацию.")
@click.option("--ssh-port", default=None, show_default=False, type=int, help="Новый SSH порт.")
@click.option("--driver-type", type=click.Choice(["ilo", "idrac"]), default=None, show_default=False, help="Новый тип BMC.")
@click.option("--ipmi-ip", "admin_panel_ip", default=None, show_default=False, help="Новый IP IPMI/iLO.")
@click.option("--ipmi-user", "admin_panel_user", default=None, show_default=False, help="Новый пользователь IPMI/iLO.")
@click.option("--status", default=None, show_default=False, help="Новый статус.")
@click.option("--os-version-id", default=None, show_default=False, type=int, help="Новый ID версии ОС.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_upd_cli(
    server_query: str | None,
    update_all: bool,
    name: str | None,
    ip_address: str | None,
    grade: str | None,
    cpu_model: str | None,
    cpu_total: int | None,
    cpu_cores_count: int | None,
    cpu_threads: int | None,
    ram_total: int | None,
    storage: str | None,
    gpu: str | None,
    phy_if: str | None,
    virtualization: bool | None,
    ssh_port: int | None,
    driver_type: str | None,
    admin_panel_ip: str | None,
    admin_panel_user: str | None,
    status: str | None,
    os_version_id: int | None,
    raw_output: bool,
):
    payload = _build_server_update_payload(
        name=name,
        ip_address=ip_address,
        grade=grade,
        cpu_model=cpu_model,
        cpu_total=cpu_total,
        cpu_cores_count=cpu_cores_count,
        cpu_threads=cpu_threads,
        ram_total=ram_total,
        storage=storage,
        gpu=gpu,
        phy_if=phy_if,
        virtualization=virtualization,
        ssh_port=ssh_port,
        driver_type=driver_type,
        admin_panel_ip=admin_panel_ip,
        admin_panel_user=admin_panel_user,
        status=status,
        os_version_id=os_version_id,
    )
    if not payload:
        raise click.UsageError("Нужно указать хотя бы одно поле для обновления.")
    if update_all and server_query:
        raise click.UsageError("Для --all не нужно указывать конкретный сервер.")
    if not update_all and not server_query:
        raise click.UsageError("Укажите сервер/номер стенда или используйте --all.")

    try:
        if update_all:
            servers = server_api.list_servers()
            if not servers:
                ui.echo("Серверы не найдены.")
                return

            click.confirm(
                f"Обновить указанные поля на всех серверах ({len(servers)} шт.)?",
                abort=True,
            )

            failed: list[str] = []
            updated_count = 0
            updated_items: list[dict] = []
            for current in sorted(servers, key=server_api.server_sort_key):
                name_or_id = str(current.get("name") or current.get("id"))
                try:
                    updated_server = server_api.update_server(current["id"], payload)
                    updated_items.append(updated_server)
                    updated_count += 1
                    ui.ok(f"{name_or_id}: обновлён.")
                except Exception as e:
                    failed.append(f"{name_or_id}: {e}")
                    ui.err(f"{name_or_id}: {e}")

            if failed:
                ui.err(f"Не удалось обновить {len(failed)} сервер(ов).")
                sys.exit(1)

            if raw_output:
                _print_raw_json(updated_items)
                return

            ui.ok(f"Обновлено серверов: {updated_count}.")
            return

        current = server_api.resolve_server(server_query)
        server = server_api.update_server(current["id"], payload)
        if raw_output:
            _print_raw_json(server)
            return
        ui.ok(f"Сервер '{server.get('name')}' обновлён.")
        _render_server_view(server)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@server_group.command("del", short_help="Удалить сервер.")
@click.argument("server_query", metavar="SERVER")
@click.option("--yes", is_flag=True, help="Удалить без подтверждения.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_del_cli(server_query: str, yes: bool, raw_output: bool):
    try:
        server = server_api.resolve_server(server_query)
        if not yes:
            click.confirm(f"Удалить сервер '{server.get('name')}'?", abort=True)
        server_api.delete_server(server["id"])
        if raw_output:
            _print_raw_json(
                {
                    "deleted": True,
                    "id": server.get("id"),
                    "name": server.get("name"),
                }
            )
            return
        ui.ok(f"Сервер '{server.get('name')}' удалён.")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@server_group.command("passwd", short_help="Сменить пароль IPMI/iLO.")
@click.argument("password_target", metavar="TARGET", type=click.Choice(["ipmi"]))
@click.argument("arg1", required=False, metavar="[SERVER_OR_PASSWORD]")
@click.argument("arg2", required=False, metavar="[PASSWORD]")
@click.option("--all", "change_all", is_flag=True, help="Сменить пароль на всех серверах.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_passwd_cli(
    password_target: str,
    arg1: str | None,
    arg2: str | None,
    change_all: bool,
    raw_output: bool,
):
    field_name = "admin_panel_pass"
    label = "IPMI/iLO"

    if change_all:
        if arg2 is not None:
            raise click.UsageError("Для --all допустим только один позиционный аргумент: PASSWORD.")
        new_password = arg1 or _prompt_hidden_password(f"Введите новый пароль {label}")
        try:
            servers = server_api.list_servers()
            if not servers:
                ui.echo("Серверы не найдены.")
                return

            click.confirm(
                f"Сменить пароль {label} на всех серверах ({len(servers)} шт.)?",
                abort=True,
            )

            failed: list[str] = []
            updated_items: list[dict] = []
            for server in sorted(servers, key=server_api.server_sort_key):
                name = str(server.get("name") or server.get("id"))
                try:
                    updated = server_api.update_server(server["id"], {field_name: new_password})
                    updated_items.append(updated)
                    ui.ok(f"{name}: пароль обновлён.")
                except Exception as e:
                    failed.append(f"{name}: {e}")
                    ui.err(f"{name}: {e}")

            if failed:
                ui.err(f"Не удалось обновить {len(failed)} сервер(ов).")
                sys.exit(1)

            if raw_output:
                _print_raw_json(updated_items)
                return

            ui.ok("Пароли обновлены на всех серверах.")
        except Exception as e:
            ui.err(f"Ошибка: {e}")
            sys.exit(1)
        return

    server_query = arg1
    if not server_query:
        raise click.UsageError("Укажите сервер или номер стенда, либо используйте --all.")
    new_password = arg2 or _prompt_hidden_password(f"Введите новый пароль {label}")

    try:
        server = server_api.resolve_server(server_query)
        updated = server_api.update_server(server["id"], {field_name: new_password})
        if raw_output:
            _print_raw_json(updated)
            return
        ui.ok(f"Пароль {label} для '{updated.get('name')}' обновлён.")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


def _server_power_action(server_query: str, action: str) -> dict:
    try:
        server = server_api.resolve_server(server_query)
        server_id = server["id"]
        if action == "start":
            result = server_api.power_on_server(server_id)
            return result if isinstance(result, dict) else {"id": server_id, "name": server.get("name")}
        if action == "stop":
            result = server_api.power_off_server(server_id)
            return result if isinstance(result, dict) else {"id": server_id, "name": server.get("name")}
        if action == "reboot":
            result = server_api.reboot_server(server_id)
            return result if isinstance(result, dict) else {"id": server_id, "name": server.get("name")}
        raise click.UsageError(f"Неизвестное действие сервера: {action}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@server_group.command("start", short_help="Включить сервер через server API/IPMI.")
@click.argument("server_query", metavar="SERVER")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_start_cli(server_query: str, raw_output: bool):
    result = _server_power_action(server_query, "start")
    if raw_output:
        _print_raw_json(result)
        return
    ui.ok(f"Сервер '{result.get('name') or server_query}' включается.")


@server_group.command("stop", short_help="Выключить сервер через server API/IPMI.")
@click.argument("server_query", metavar="SERVER")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_stop_cli(server_query: str, raw_output: bool):
    result = _server_power_action(server_query, "stop")
    if raw_output:
        _print_raw_json(result)
        return
    ui.ok(f"Сервер '{result.get('name') or server_query}' выключается.")


@server_group.command("reboot", short_help="Перезагрузить сервер через server API/IPMI.")
@click.argument("server_query", metavar="SERVER")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_reboot_cli(server_query: str, raw_output: bool):
    result = _server_power_action(server_query, "reboot")
    if raw_output:
        _print_raw_json(result)
        return
    ui.ok(f"Сервер '{result.get('name') or server_query}' перезагружается.")


@server_group.command("on", short_help="Алиас для start.", hidden=True)
@click.argument("server_query", metavar="SERVER")
def server_on_cli(server_query: str):
    result = _server_power_action(server_query, "start")
    ui.ok(f"Сервер '{result.get('name') or server_query}' включается.")


@server_group.command("off", short_help="Алиас для stop.", hidden=True)
@click.argument("server_query", metavar="SERVER")
def server_off_cli(server_query: str):
    result = _server_power_action(server_query, "stop")
    ui.ok(f"Сервер '{result.get('name') or server_query}' выключается.")


@server_group.command("release", short_help="Освободить сервер.")
@click.argument("server_query", metavar="SERVER")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_release_cli(server_query: str, raw_output: bool):
    try:
        server = server_api.resolve_server(server_query)
        server_id = server["id"]
        result = server_api.release_server(server_id)
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok(f"Сервер '{server.get('name')}' переведён в статус free.")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@server_group.command("os-refresh", short_help="Обновить список версий ОС из внешнего API.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_os_refresh_cli(raw_output: bool):
    try:
        result = server_api.refresh_os_versions()
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)

    if raw_output:
        import json as _json
        ui.echo(_json.dumps(result, ensure_ascii=False, indent=2))
        return

    ui.ok("Список версий ОС обновлён.")
    ui.table(
        headers=["Source URL", "Added", "Total"],
        rows=[[
            result.get("source_url", ""),
            result.get("added", 0),
            result.get("total", 0),
        ]],
    )


@server_group.command("os-set", short_help="Установить версию ОС по имени версии.")
@click.argument("server_query", metavar="SERVER")
@click.argument("os_version_name", metavar="OS_VERSION")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_os_set_cli(server_query: str, os_version_name: str, raw_output: bool):
    try:
        server = server_api.resolve_server(server_query, enrich=False)
        server_name = str(server.get("name") or "").strip()
        if not server_name:
            raise click.ClickException(f"Не удалось определить имя сервера из '{server_query}'.")
        updated = server_api.update_server_os_version_by_name(server_name, os_version_name)
        if raw_output:
            _print_raw_json(updated)
            return
        ui.ok(f"Для сервера '{updated.get('name')}' установлена версия ОС '{updated.get('os_version') or ''}'.")
        _render_server_view(updated)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@server_snapshot_password_group.command("list", short_help="Показать все пароли версий ОС.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_snapshot_password_list_cli(raw_output: bool):
    try:
        items = server_api.list_snapshot_passwords()
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)

    if raw_output:
        import json as _json
        ui.echo(_json.dumps(items, ensure_ascii=False, indent=2))
        return

    if not items:
        ui.echo("Список паролей версий ОС пуст.")
        return

    ui.table(
        headers=["OS Version", "SSH User", "Password", "Updated By", "Updated At"],
        rows=_snapshot_password_rows(items),
    )


@server_snapshot_password_group.command("get", short_help="Показать пароль по имени версии ОС.")
@click.argument("os_version_name", metavar="OS_VERSION")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_snapshot_password_get_cli(os_version_name: str, raw_output: bool):
    try:
        item = server_api.get_snapshot_password(os_version_name)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)

    if raw_output:
        import json as _json
        ui.echo(_json.dumps(item, ensure_ascii=False, indent=2))
        return

    ui.table(
        headers=["OS Version", "SSH User", "Password", "Updated By", "Updated At"],
        rows=_snapshot_password_rows([item]),
    )


@server_snapshot_password_group.command("set", short_help="Создать или обновить пароль для версии ОС.")
@click.argument("os_version_name", metavar="OS_VERSION")
@click.option("--password", default=None, show_default=False, help="Пароль для версии ОС.")
@click.option("--ssh-user", "ssh_username", default=None, show_default=False, help="SSH пользователь версии ОС.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_snapshot_password_set_cli(
    os_version_name: str,
    password: str | None,
    ssh_username: str | None,
    raw_output: bool,
):
    pwd = password or _prompt_hidden_password("Введите пароль для версии ОС")
    user = (ssh_username or click.prompt("Введите SSH пользователя для версии ОС", default="u", show_default=True)).strip()
    try:
        item = server_api.upsert_snapshot_password(os_version_name, pwd, user)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)

    if raw_output:
        import json as _json
        ui.echo(_json.dumps(item, ensure_ascii=False, indent=2))
        return

    ui.ok(f"Пароль для версии ОС '{item['os_version_name']}' сохранён.")
    ui.table(
        headers=["OS Version", "SSH User", "Password", "Updated By", "Updated At"],
        rows=_snapshot_password_rows([item]),
    )


@server_snapshot_password_group.command("upd", short_help="Обновить существующий пароль версии ОС.")
@click.argument("os_version_name", metavar="OS_VERSION")
@click.option("--password", default=None, show_default=False, help="Новый пароль.")
@click.option("--ssh-user", "ssh_username", default=None, show_default=False, help="Новый SSH пользователь.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_snapshot_password_update_cli(
    os_version_name: str,
    password: str | None,
    ssh_username: str | None,
    raw_output: bool,
):
    pwd = password.strip() if password is not None else None
    user = ssh_username.strip() if ssh_username is not None else None
    if pwd is None and user is None:
        pwd = _prompt_hidden_password("Введите новый пароль для версии ОС")
    try:
        item = server_api.update_snapshot_password(
            os_version_name,
            password=pwd,
            ssh_username=user,
        )
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)

    if raw_output:
        import json as _json
        ui.echo(_json.dumps(item, ensure_ascii=False, indent=2))
        return

    ui.ok(f"Пароль для версии ОС '{item['os_version_name']}' обновлён.")
    ui.table(
        headers=["OS Version", "SSH User", "Password", "Updated By", "Updated At"],
        rows=_snapshot_password_rows([item]),
    )


@server_snapshot_password_group.command("del", short_help="Удалить пароль версии ОС.")
@click.argument("os_version_name", metavar="OS_VERSION")
@click.option("--yes", is_flag=True, help="Удалить без подтверждения.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def server_snapshot_password_delete_cli(os_version_name: str, yes: bool, raw_output: bool):
    try:
        if not yes:
            click.confirm(f"Удалить пароль для версии ОС '{os_version_name}'?", abort=True)
        server_api.delete_snapshot_password(os_version_name)
        if raw_output:
            _print_raw_json({"deleted": True, "os_version_name": os_version_name})
            return
        ui.ok(f"Пароль для версии ОС '{os_version_name}' удалён.")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


def _warn_ssh_password_state(target: dict, *, entity_label: str) -> None:
    password = str(target.get("server_password") or "").strip()
    if password == "***hidden***":
        ui.warn(f"Пароль {entity_label} скрыт правами доступа. Выполняю обычный ssh без автологина.")
    elif not password:
        ui.warn(f"Пароль {entity_label} не найден. Выполняю обычный ssh без автологина.")


def _parse_ssh_target_query(value: str) -> tuple[str, str]:
    query = value.strip()
    if not query:
        raise click.UsageError("TARGET не должен быть пустым.")

    prefix, sep, target = query.partition(":")
    if not sep:
        return "auto", query

    mode = prefix.strip().lower()
    target_value = target.strip()
    if not target_value:
        raise click.UsageError("После префикса TARGET должен быть непустым (например, vm:stand12_srv-main).")

    if mode in {"server", "srv"}:
        return "server", target_value
    if mode == "vm":
        return "vm", target_value
    if mode in {"local", "local-vm", "lvm"}:
        return "local", target_value
    return "auto", query


@cli.command(
    "ssh",
    short_help="Подключиться по SSH к серверу или ВМ.",
    help=(
        "Подключение по SSH с авто-резолвом в порядке: server -> vm -> local vm.\n"
        "Можно явно указать тип цели:\n"
        "- server:<имя_сервера_или_стенд>\n"
        "- vm:<имя_вм_или_имя_сервера_или_стенд>\n"
        "- local:<имя_local_vm>\n"
    ),
)
@click.argument("target_query", metavar="TARGET")
def ssh_cli(target_query: str):
    mode, target = _parse_ssh_target_query(target_query)

    # 1) API-ресурсы (server -> vm)
    can_use_api = True
    auth_error: Exception | None = None
    try:
        auth_utils.load_token(verbose=False)
    except auth_utils.AuthError as e:
        can_use_api = False
        auth_error = e

    server_error: Exception | None = None
    vm_error: Exception | None = None
    if can_use_api:
        if mode in {"auto", "server"}:
            try:
                server = server_api.resolve_server(target)
                _warn_ssh_password_state(server, entity_label="сервера")
                server_api.exec_ssh(server)
                return
            except Exception as e:
                server_error = e
                if mode == "server":
                    ui.err(f"Не удалось найти server '{target}': {e}")
                    sys.exit(1)

        if mode in {"auto", "vm"}:
            try:
                vm_target = vm_api.resolve_vm_ssh_target(target)
                _warn_ssh_password_state(vm_target, entity_label="ВМ")
                server_api.exec_ssh(vm_target)
                return
            except Exception as e:
                vm_error = e
                if mode == "vm":
                    ui.err(f"Не удалось найти vm '{target}': {e}")
                    sys.exit(1)
    elif mode in {"server", "vm"}:
        ui.err(
            f"Для TARGET '{target_query}' нужен доступ к API, но авторизация недоступна: {auth_error}"
        )
        sys.exit(1)

    # 2) local VM fallback
    if mode not in {"auto", "local"}:
        ui.err(f"Не удалось найти TARGET='{target_query}'. server: {server_error}; vm: {vm_error}")
        sys.exit(1)

    try:
        local_vm_target = vm_local_api.resolve_vm_ssh_target(target)
        _warn_ssh_password_state(local_vm_target, entity_label="local VM")
        server_api.exec_ssh(local_vm_target)
        return
    except vm_local_api.LocalVMError as local_error:
        if can_use_api:
            ui.err(
                f"Не удалось найти TARGET='{target_query}' как сервер, ВМ или local VM. "
                f"server: {server_error}; vm: {vm_error}; local vm: {local_error}"
            )
        else:
            ui.err(
                f"Не удалось найти local VM '{target}'. "
                f"API-резолв (server -> vm) пропущен: {auth_error}"
            )
        sys.exit(1)
    except Exception as e:
        ui.err(f"Ошибка SSH (local VM): {e}")
        sys.exit(1)


@local_group.command(
    "ssh",
    short_help="Подключиться по SSH только к local VM.",
    help=(
        "SSH только к local VM (без fallback на server/vm API).\n"
        "Локальный inventory читается из ~/.config/allta/local_vm/:\n"
        "- vms.json\n"
        "- snapshots.json\n"
        "- prepare.json\n"
        "- provider_vms_dates.json\n\n"
        "ВМ поднимаются в системном libvirt пользователя root.\n"
        "Проверки и ручные операции virsh выполняйте через sudo "
        "(например: sudo virsh -c qemu:///system domstate <vm>)."
    ),
)
@click.argument("target_query", metavar="VM")
def local_ssh_cli(target_query: str):
    try:
        local_vm_target = vm_local_api.resolve_vm_ssh_target(target_query)
        _warn_ssh_password_state(local_vm_target, entity_label="local VM")
        server_api.exec_ssh(local_vm_target)
    except Exception as e:
        ui.err(f"Ошибка SSH (local VM): {e}")
        sys.exit(1)


@vm_group.command("list", short_help="Показать все ВМ.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_list_cli(raw_output: bool):
    try:
        vms = vm_api.list_vms()
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)

    if raw_output:
        _print_raw_json(vms)
        return

    if not vms:
        ui.echo("ВМ не найдены.")
        return
    rows = []
    for vm in vms:
        rows.append([
            vm.get("name"),
            vm.get("ip_address"),
            vm.get("cpu"),
            vm.get("ram"),
            vm.get("status"),
            vm.get("password"),
        ])
    ui.table(headers=["Name", "IP", "CPU", "RAM", "Status", "Password"], rows=rows)


@vm_group.command("create", short_help="Создать ВМ (ip range=1).")
@click.option("--server-id", type=int, default=None, show_default=False, help="ID сервера.")
@click.option(
    "--server",
    "server_query",
    default=None,
    show_default=False,
    help="Имя сервера или номер стенда (например, 12-my-host или 12).",
)
@click.option("--password", required=True, help="Пароль для всех ВМ.")
@click.option(
    "--vm",
    "vms",
    multiple=True,
    required=True,
    metavar="NAME:IP:CPU:RAM",
    help="Описание ВМ. Можно указать несколько. Формат: name:ip:cpu:ram",
)
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_create_cli(
    server_id: int | None,
    server_query: str | None,
    password: str,
    vms: tuple[str, ...],
    raw_output: bool,
):
    if (server_id is None) == (server_query is None):
        raise click.UsageError("Укажите ровно один способ выбора сервера: --server-id или --server.")

    try:
        resolved_server_id = server_id
        if server_query is not None:
            server = server_api.resolve_server(server_query)
            resolved_server_id = server["id"]
            ui.step(f"Сервер '{server.get('name')}' -> id={resolved_server_id}")

        result = vm_api.create_vms(server_id=resolved_server_id, password=password, vm_specs=list(vms))
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok(f"Задача поставлена: {_task_id_or_dash(result)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("status", short_help="Показать статус ВМ по имени.")
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Вывести ответ в JSON (как раньше).")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_status_cli(name: str, json_output: bool, raw_output: bool):
    if json_output and raw_output:
        raise click.UsageError("Используйте только один флаг: --json или --raw.")
    try:
        if raw_output:
            info = vm_api.get_vm_by_name(name)
            _print_raw_json(info)
            return
        info = vm_api.status_vm(name)
        if json_output:
            _print_raw_json(info)
            return
        ui.table(
            headers=["Name", "Status", "Password"],
            rows=[[
                info.get("name") or "",
                info.get("status") or "",
                info.get("password") or "",
            ]],
        )
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("status-set", short_help="Поставить статус равным вашему логину.")
@click.argument("name")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_status_set_cli(name: str, raw_output: bool):
    try:
        result = vm_api.status_set_vm(name)
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok("Статус обновлён.")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("status-free", short_help="Освободить ВМ (сделать свободной).")
@click.argument("name")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_status_free_cli(name: str, raw_output: bool):
    try:
        result = vm_api.status_free_vm(name)
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok("ВМ освобождена.")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("start", short_help="Старт ВМ по именам.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_start_cli(vm_names: tuple[str, ...], vms: tuple[str, ...], raw_output: bool):
    try:
        result = vm_api.start_vms(_resolve_vm_names(positional=vm_names, option_values=vms))
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok(f"Задача поставлена: {_task_id_or_dash(result)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("stop", short_help="Стоп ВМ по именам.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_stop_cli(vm_names: tuple[str, ...], vms: tuple[str, ...], raw_output: bool):
    try:
        result = vm_api.stop_vms(_resolve_vm_names(positional=vm_names, option_values=vms))
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok(f"Задача поставлена: {_task_id_or_dash(result)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("astra-update", short_help="Astra update для выбранных ВМ.")
@click.option("--rc", required=True, help="Версия rc, напр. 1.8.1.6")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_astra_update_cli(rc: str, vm_names: tuple[str, ...], vms: tuple[str, ...], raw_output: bool):
    try:
        result = vm_api.astra_update(rc=rc, vm_names=_resolve_vm_names(positional=vm_names, option_values=vms))
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok(f"Задача поставлена: {_task_id_or_dash(result)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("snapshots", short_help="Список снимков по имени ВМ.")
@click.argument("name", required=False, metavar="[VM]")
@click.option("--vm", "vm_name", required=False, help="Имя ВМ")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_snapshots_cli(name: str | None, vm_name: str | None, raw_output: bool):
    try:
        snaps = vm_api.list_snapshots(_resolve_single_vm_name(positional=name, option_value=vm_name))
        if raw_output:
            _print_raw_json(snaps)
            return
        if not snaps:
            ui.echo("Снимки не найдены.")
            return
        rows = []
        for sn in snaps:
            rows.append([sn.get("id"), sn.get("name")])
        ui.table(headers=["ID", "Name"], rows=rows)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("snapshot-create", short_help="Создать снимок для ВМ (по именам).")
@click.option("--name", "snap_name", required=True, help="Имя снимка.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_snapshot_create_cli(snap_name: str, vm_names: tuple[str, ...], vms: tuple[str, ...], raw_output: bool):
    try:
        result = vm_api.create_snapshot(
            vm_names=_resolve_vm_names(positional=vm_names, option_values=vms),
            snap_name=snap_name,
        )
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok(f"Задача поставлена: {_task_id_or_dash(result)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("snapshot-delete", short_help="Удалить снимок по именам ВМ.")
@click.option("--name", "snap_name", required=True, help="Имя снимка.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_snapshot_delete_cli(snap_name: str, vm_names: tuple[str, ...], vms: tuple[str, ...], raw_output: bool):
    try:
        result = vm_api.delete_snapshot(
            vm_names=_resolve_vm_names(positional=vm_names, option_values=vms),
            snap_name=snap_name,
        )
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok(f"Задача поставлена: {_task_id_or_dash(result)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@vm_group.command("snapshot-revert", short_help="Откатить ВМ к снимку (по именам).")
@click.option("--name", "snap_name", required=True, help="Имя снимка.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
@click.option("--raw", "raw_output", is_flag=True, help="Вывести ответ в JSON.")
def vm_snapshot_revert_cli(snap_name: str, vm_names: tuple[str, ...], vms: tuple[str, ...], raw_output: bool):
    try:
        result = vm_api.revert_snapshot(
            vm_names=_resolve_vm_names(positional=vm_names, option_values=vms),
            snap_name=snap_name,
        )
        if raw_output:
            _print_raw_json(result)
            return
        ui.ok(f"Задача поставлена: {_task_id_or_dash(result)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


def _resolve_local_vm_basename(positional: str | None, option_value: str | None) -> str:
    pos = positional.strip() if positional else None
    opt = option_value.strip() if option_value else None
    if pos and opt and pos != opt:
        raise click.UsageError("Базовое имя ВМ указано дважды по-разному: позиционно и через --name.")
    base = opt or pos
    return base or "testvm1"


@local_vm_group.command("build", short_help="Собрать локальные ВМ через allta_lib.")
@click.argument("basename", required=False, metavar="[BASENAME]")
@click.option("--name", "base_name_opt", required=False, help="Базовое имя ВМ (по умолчанию testvm1).")
@click.option("--count", required=False, default=1, show_default=True, type=click.IntRange(min=1), help="Количество ВМ (минимум 1).")
@click.option("--cpu", required=False, default=4, show_default=True, type=click.IntRange(min=1), help="CPU на ВМ (минимум 1).")
@click.option("--ram", required=False, default=4, show_default=True, type=click.IntRange(min=2), help="RAM на ВМ (GB, минимум 2).")
@click.option("--rc", required=True, help="Версия RC.")
@click.option("--disk", "disk_size", required=False, default=20, show_default=True, type=click.IntRange(min=11), help="Размер диска (минимум 11).")
@click.option("--box", required=False, default=None, help="Имя box для allta_lib. Если не задано: автоподбор по rc.")
def local_vm_build_cli(
    basename: str | None,
    base_name_opt: str | None,
    count: int,
    cpu: int,
    ram: int,
    rc: str,
    disk_size: int,
    box: str | None,
):
    try:
        base_name = _resolve_local_vm_basename(positional=basename, option_value=base_name_opt)
        created = vm_local_api.build_vms(
            vm_name=base_name,
            vm_count=count,
            cpu=cpu,
            ram=ram,
            disk_size=disk_size,
            rc=rc,
            box=box,
        )
        if created:
            ui.ok(f"Созданы local VM: {', '.join(created)}")
        else:
            ui.ok("Сборка local VM завершена.")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("b", hidden=True)
@click.argument("basename", required=False, metavar="[BASENAME]")
@click.option("--name", "base_name_opt", required=False, help="Базовое имя ВМ (по умолчанию testvm1).")
@click.option("--count", required=False, default=1, show_default=True, type=click.IntRange(min=1), help="Количество ВМ (минимум 1).")
@click.option("--cpu", required=False, default=4, show_default=True, type=click.IntRange(min=1), help="CPU на ВМ (минимум 1).")
@click.option("--ram", required=False, default=4, show_default=True, type=click.IntRange(min=2), help="RAM на ВМ (GB, минимум 2).")
@click.option("--rc", required=True, help="Версия RC.")
@click.option("--disk", "disk_size", required=False, default=20, show_default=True, type=click.IntRange(min=11), help="Размер диска (минимум 11).")
@click.option("--box", required=False, default=None, help="Имя box для allta_lib. Если не задано: автоподбор по rc.")
def local_vm_build_alias_cli(
    basename: str | None,
    base_name_opt: str | None,
    count: int,
    cpu: int,
    ram: int,
    rc: str,
    disk_size: int,
    box: str | None,
):
    local_vm_build_cli(
        basename=basename,
        base_name_opt=base_name_opt,
        count=count,
        cpu=cpu,
        ram=ram,
        rc=rc,
        disk_size=disk_size,
        box=box,
    )


@local_vm_group.command("list", short_help="Показать локальные ВМ.")
def local_vm_list_cli():
    try:
        vms = vm_local_api.list_vms()
        if not vms:
            ui.echo("Локальные ВМ не найдены.")
            return
        rows = []
        for vm in vms:
            rows.append([
                vm.get("name", ""),
                vm.get("ip_address", ""),
                vm.get("ip_bridge", ""),
                vm.get("cpu", ""),
                vm.get("ram", ""),
                vm.get("status", ""),
                vm.get("password", ""),
            ])
        ui.table(headers=["Name", "IP", "IP Bridge", "CPU", "RAM", "Status", "Password"], rows=rows)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("status", short_help="Показать статус локальной ВМ.")
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Вывести ответ в JSON.")
def local_vm_status_cli(name: str, json_output: bool):
    try:
        info = vm_local_api.status_vm(name)
        if json_output:
            import json as _json
            ui.echo(_json.dumps(info, ensure_ascii=False, indent=2))
            return
        ui.table(
            headers=["Name", "Status", "Password"],
            rows=[[
                info.get("name") or "",
                info.get("status") or "",
                info.get("password") or "",
            ]],
        )
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("start", short_help="Старт локальных ВМ по именам.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
def local_vm_start_cli(vm_names: tuple[str, ...], vms: tuple[str, ...]):
    try:
        names = _resolve_vm_names(positional=vm_names, option_values=vms)
        vm_local_api.start_vms(names)
        ui.ok(f"Запущены local VM: {', '.join(names)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("stop", short_help="Стоп локальных ВМ по именам.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
def local_vm_stop_cli(vm_names: tuple[str, ...], vms: tuple[str, ...]):
    try:
        names = _resolve_vm_names(positional=vm_names, option_values=vms)
        vm_local_api.stop_vms(names)
        ui.ok(f"Остановлены local VM: {', '.join(names)}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("astra-update", short_help="Astra update для local VM.")
@click.option("--rc", required=True, help="Версия rc, напр. 1.8.1.6")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
def local_vm_astra_update_cli(rc: str, vm_names: tuple[str, ...], vms: tuple[str, ...]):
    try:
        names = _resolve_vm_names(positional=vm_names, option_values=vms)
        task_id = vm_local_api.astra_update(rc=rc, vm_names=names)
        ui.ok(f"Операция выполнена: {task_id}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("snapshots", short_help="Список снимков local VM.")
@click.argument("name", required=False, metavar="[VM]")
@click.option("--vm", "vm_name", required=False, help="Имя ВМ")
def local_vm_snapshots_cli(name: str | None, vm_name: str | None):
    try:
        snapshots = vm_local_api.list_snapshots(_resolve_single_vm_name(positional=name, option_value=vm_name))
        if not snapshots:
            ui.echo("Снимки не найдены.")
            return
        rows = []
        for snap in snapshots:
            rows.append([snap.get("id"), snap.get("name")])
        ui.table(headers=["ID", "Name"], rows=rows)
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("snapshot-create", short_help="Создать snapshot для local VM.")
@click.option("--name", "snap_name", required=True, help="Имя снимка.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
def local_vm_snapshot_create_cli(snap_name: str, vm_names: tuple[str, ...], vms: tuple[str, ...]):
    try:
        names = _resolve_vm_names(positional=vm_names, option_values=vms)
        task_id = vm_local_api.create_snapshot(vm_names=names, snap_name=snap_name)
        ui.ok(f"Операция выполнена: {task_id}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("snapshot-delete", short_help="Удалить snapshot для local VM.")
@click.option("--name", "snap_name", required=True, help="Имя снимка.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
def local_vm_snapshot_delete_cli(snap_name: str, vm_names: tuple[str, ...], vms: tuple[str, ...]):
    try:
        names = _resolve_vm_names(positional=vm_names, option_values=vms)
        task_id = vm_local_api.delete_snapshot(vm_names=names, snap_name=snap_name)
        ui.ok(f"Операция выполнена: {task_id}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@local_vm_group.command("snapshot-revert", short_help="Откатить local VM к snapshot.")
@click.option("--name", "snap_name", required=True, help="Имя снимка.")
@click.argument("vm_names", nargs=-1, metavar="[VM]...")
@click.option("--vms", multiple=True, metavar="VM[,VM2,...]", help="Имена ВМ. Можно через запятую и вместе с позиционными.")
def local_vm_snapshot_revert_cli(snap_name: str, vm_names: tuple[str, ...], vms: tuple[str, ...]):
    try:
        names = _resolve_vm_names(positional=vm_names, option_values=vms)
        task_id = vm_local_api.revert_snapshot(vm_names=names, snap_name=snap_name)
        ui.ok(f"Операция выполнена: {task_id}")
    except Exception as e:
        ui.err(f"Ошибка: {e}")
        sys.exit(1)


@mc_group.command("111", short_help="MC на QA FTP.")
def mc_qa_cli():
    with ui.section("MC • QA"):
        rc = mc_cmd.mc_qa()
        sys.exit(rc)

@mc_group.command("10", short_help="MC на ftp://10.177.103.10/.")
def mc_10_cli():
    with ui.section("MC • 10.177.103.10"):
        rc = mc_cmd.mc_10()
        sys.exit(rc)

@mc_group.command("ci", short_help="MC на CI FTP.")
def mc_ci_cli():
    with ui.section("MC • CI"):
        rc = mc_cmd.mc_ci()
        sys.exit(rc)


if __name__ == "__main__":
    cli()
