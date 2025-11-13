from __future__ import annotations
import sys
import click

from allta_cli.utils import ui
from allta_cli.utils import auth as auth_utils
from allta_cli.commands.tokens import tokens_cmd as tokens_run
from allta_cli.commands.files import files_cmd as files_run, boxes_cmd, releases_cmd
from allta_cli.commands.git import git_clone
from allta_cli.commands.python import install_python, create_venv
from allta_cli.commands import mc as mc_cmd


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


# --- Группа с предсказуемым порядком и авто-help ---
class OrderedGroup(click.Group):
    def __init__(self, *a, **kw):
        kw.setdefault("no_args_is_help", True)
        super().__init__(*a, **kw)

    def list_commands(self, ctx):
        order = [
            "login", "logout",
            "git", "mc", "tokens", "files",
            "boxes", "releases",
            "python", "venv",
        ]
        existing = super().list_commands(ctx)
        return [c for c in order if c in existing] + [c for c in existing if c not in order]


def _do_post_login_actions(*, git: bool = False, tokens: bool = False, boxes_flag: bool = False, releases_flag: bool = False):
    """Пост-действия сразу после входа."""
    # ВНИМАНИЕ: сами called-команды уже печатают свои разделители (git/boxes/releases/tokens/files).
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
        "Быстрые вызовы без входа:\n"
        "  allta --releases | allta -r\n"
        "  allta --boxes    | allta -b\n\n"
        "Шорткат входа:\n"
        "  allta -l -u USER -p PASS [--git] [--tokens] [-b] [-r]\n"
    ),
    epilog=(
        "Примеры:\n"
        "  allta login -u user -p pass --tokens\n"
        "  allta tokens git_token\n"
        "  allta files releases.json\n"
        "  allta git\n"
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
              help="Пароль для -l.")
@click.option("--git", is_flag=True, help="После входа: выполнить git clone.")
@click.option("--tokens", is_flag=True, help="После входа: получить tokens.json.")
@click.option("-b", "--boxes", "boxes_flag", is_flag=True,
              help="Показать имена боксов (без входа).")
@click.option("-r", "--releases", "releases_flag", is_flag=True,
              help="Показать версии releases (без входа).")
@click.pass_context
def cli(ctx: click.Context,
        login_flag: bool,
        top_user: str | None,
        top_password: str | None,
        git: bool, tokens: bool, boxes_flag: bool, releases_flag: bool):
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
    user = top_user
    pwd = top_password
    if not user or not pwd:
        raise click.UsageError("Для -l укажите USERNAME и PASSWORD через -u/-p.")

    with ui.section("LOGIN"):
        try:
            ui.http("POST http://allta.devos.astralinux.ru:21500/api/auth/login")
            auth_utils.login(login=user, password=pwd, verbose=True)
            ui.ok("Вход выполнен (shortcut -l).")
        except auth_utils.AuthError as e:
            ui.err(f"Ошибка входа: {e}")
            ctx.exit(1)

    _do_post_login_actions(git=git, tokens=tokens, boxes_flag=boxes_flag, releases_flag=releases_flag)


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

@cli.command("tokens", short_help="Получить tokens.json.",
             help="Получить tokens.json целиком или значение конкретного ключа.")
@click.argument("token_type", required=False, metavar="[KEY]")
@with_section("TOKENS")
def tokens_cli(token_type: str | None):
    code = tokens_run(token_type)
    sys.exit(code)

@cli.command("files", short_help="Скачать JSON-файл из config-API.",
             help="Получить JSON-файл из config-API (например, releases.json).")
@click.argument("filename", required=True, metavar="FILENAME")
@with_section("FILES")
def files_cli(filename: str):
    code = files_run(filename)
    sys.exit(code)

@cli.command("login", short_help="Авторизоваться.",
             help="Авторизоваться (позиционные аргументы или флаги -u/-p).")
@click.argument("username", required=False, metavar="[USERNAME]")
@click.argument("password", required=False, metavar="[PASSWORD]")
@click.option("-u", "--user", "--username", "--usermane", "user_opt",
              metavar="USERNAME", default=None, show_default=False,
              help="Логин (если не указан позиционно).")
@click.option("-p", "--password", "pass_opt",
              metavar="PASSWORD", default=None, show_default=False,
              help="Пароль (если не указан позиционно).")
@click.option("--git", is_flag=True, help="После входа: git clone.")
@click.option("--tokens", is_flag=True, help="После входа: получить tokens.json.")
@click.option("-b", "--boxes", "boxes_flag", is_flag=True, help="После входа: показать boxes.")
@click.option("-r", "--releases", "releases_flag", is_flag=True, help="После входа: показать releases.")
@with_section("LOGIN")
def login_cmd(username: str | None, password: str | None,
              user_opt: str | None, pass_opt: str | None,
              git: bool, tokens: bool, boxes_flag: bool, releases_flag: bool):
    user = user_opt or username
    pwd = pass_opt or password
    if not user or not pwd:
        raise click.UsageError("Нужно указать логин и пароль (позиционно или через -u/-p).")
    try:
        ui.http("POST http://allta.devos.astralinux.ru:21500/api/auth/login")
        auth_utils.login(login=user, password=pwd, verbose=True)
        ui.ok("Вход выполнен.")
    except auth_utils.AuthError as e:
        ui.err(f"Ошибка входа: {e}")
        sys.exit(1)
    _do_post_login_actions(git=git, tokens=tokens, boxes_flag=boxes_flag, releases_flag=releases_flag)

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

@cli.group("mc", short_help="Открыть MC на преднастроенных FTP.")
def mc_group():
    """Открыть Midnight Commander на преднастроенных FTP-хостах."""

@mc_group.command("111", short_help="MC на QA FTP.")
def mc_qa_cli():
    with ui.section("MC • QA"):
        rc = mc_cmd.mc_qa()
        sys.exit(rc)

@mc_group.command("ci", short_help="MC на CI FTP.")
def mc_ci_cli():
    with ui.section("MC • CI"):
        rc = mc_cmd.mc_ci()
        sys.exit(rc)


if __name__ == "__main__":
    cli()
