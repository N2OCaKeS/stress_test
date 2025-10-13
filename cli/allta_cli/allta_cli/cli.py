from __future__ import annotations
import sys
import click

from allta_cli.utils import auth as auth_utils
from allta_cli.commands.tokens import tokens_cmd as tokens_run
from allta_cli.commands.files import files_cmd as files_run, boxes_cmd, releases_cmd
from allta_cli.commands.git import git_clone
from allta_cli.commands.python import install_python, create_venv


def _do_post_login_actions(*, git: bool = False, tokens: bool = False, boxes_flag: bool = False, releases_flag: bool = False):
    """Пост-действия сразу после входа."""
    if git:
        click.secho("⟶ Выполняю git clone…", fg="yellow")
        rc = git_clone()
        if rc != 0:
            click.secho(f"git clone завершился с кодом {rc}", fg="red")
            sys.exit(rc)

    if tokens:
        click.secho("⟶ Получаю tokens.json…", fg="yellow")
        code = tokens_run(None)
        if code != 0:
            sys.exit(code)

    if boxes_flag:
        click.secho("⟶ Получаю test-box-config.json…", fg="yellow")
        code = boxes_cmd()
        if code != 0:
            sys.exit(code)

    if releases_flag:
        click.secho("⟶ Получаю releases.json…", fg="yellow")
        code = releases_cmd()
        if code != 0:
            sys.exit(code)


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.version_option(version="0.1.0", prog_name="allta")
# шорткат верхнего уровня: allta -l -u ... -p ... [флаги]
@click.option("-l", "--login", "login_flag", is_flag=True, help="Войти без явной сабкоманды (короткая форма).")
@click.option("-u", "--user", "--username", "--usermane", "top_user", metavar="USERNAME", default=None)
@click.option("-p", "--password", "top_password", metavar="PASSWORD", default=None)
# пост-действия и автономные флаги
@click.option("--git", is_flag=True, help="После входа: git clone.")
@click.option("--tokens", is_flag=True, help="После входа: получить tokens.json.")
@click.option("-b", "--boxes", "boxes_flag", is_flag=True, help="Показать имена боксов (можно и без входа).")
@click.option("-r", "--releases", "releases_flag", is_flag=True, help="Показать версии releases (можно и без входа).")
@click.pass_context
def cli(ctx: click.Context,
        login_flag: bool,
        top_user: str | None,
        top_password: str | None,
        git: bool, tokens: bool, boxes_flag: bool, releases_flag: bool):
    """allta — CLI.

    Поддерживаемые формы входа:
      allta login USER PASS
      allta login -u USER -p PASS
      allta -l -u USER -p PASS

    Быстрые вызовы без входа:
      allta --releases     | allta -r
      allta --boxes        | allta -b

    Отдельные команды:
      allta releases
      allta boxes
    """
    # если вызвали сабкоманду — отдаём управление ей
    if ctx.invoked_subcommand is not None:
        return

    # без сабкоманды: если есть -b/-r, выполняем их без входа
    if not login_flag:
        ran_something = False
        if boxes_flag:
            print("⟶ Получаю test-box-config.json…")
            code = boxes_cmd()
            if code != 0:
                sys.exit(code)
            ran_something = True

        if releases_flag:
            print("⟶ Получаю releases.json…")
            code = releases_cmd()
            if code != 0:
                sys.exit(code)
            ran_something = True

        if ran_something:
            return

        # ничего не попросили — показываем help
        if not ctx.resilient_parsing:
            click.echo(ctx.get_help())
        return

    # режим шортката входа (-l)
    user = top_user
    pwd = top_password
    if not user or not pwd:
        raise click.UsageError("Для -l укажите USERNAME и PASSWORD через -u/-p.")

    # логин
    try:
        print("→ POST http://allta.devos.astralinux.ru/api/auth/login")
        auth_utils.login(login=user, password=pwd, verbose=True)
        click.secho("✓ Вход выполнен (shortcut -l).", fg="green")
    except auth_utils.AuthError as e:
        click.secho(f"Ошибка входа: {e}", fg="red")
        ctx.exit(1)

    # пост-действия (включая -b/-r, если их передали вместе с -l)
    _do_post_login_actions(git=git, tokens=tokens, boxes_flag=boxes_flag, releases_flag=releases_flag)


# --- отдельные команды ---

@cli.command("releases", help="Показать доступные версии из releases.json (вход не обязателен).")
def releases_cli():
    print("⟶ Получаю releases.json…")
    code = releases_cmd()
    sys.exit(code)


@cli.command("boxes", help="Показать имена боксов (libvirt_box) из test-box-config.json (вход не обязателен).")
def boxes_cli():
    print("⟶ Получаю test-box-config.json…")
    code = boxes_cmd()
    sys.exit(code)


# --- прочие команды как были ---

@cli.command("git", help="Клонировать фиксированный репозиторий (через токен из tokens.json).")
def git_cmd():
    rc = git_clone()
    if rc != 0:
        sys.exit(rc)

@cli.command("tokens", help="Получить tokens.json (целиком или по ключу).")
@click.argument("token_type", required=False)
def tokens_cli(token_type: str | None):
    code = tokens_run(token_type)
    sys.exit(code)

@cli.command("files", help="Получить JSON-файл из config-API (например, releases.json).")
@click.argument("filename", required=True)
def files_cli(filename: str):
    code = files_run(filename)
    sys.exit(code)

@cli.command("login", help="Авторизироваться")
@click.argument("username", required=False)
@click.argument("password", required=False)
@click.option("-u", "--user", "--username", "--usermane", "user_opt", metavar="USERNAME", default=None)
@click.option("-p", "--password", "pass_opt", metavar="PASSWORD", default=None)
@click.option("--git", is_flag=True, help="После входа: git clone.")
@click.option("--tokens", is_flag=True, help="После входа: получить tokens.json.")
@click.option("-b", "--boxes", "boxes_flag", is_flag=True, help="После входа: показать boxes.")
@click.option("-r", "--releases", "releases_flag", is_flag=True, help="После входа: показать releases.")
def login_cmd(username: str | None, password: str | None,
              user_opt: str | None, pass_opt: str | None,
              git: bool, tokens: bool, boxes_flag: bool, releases_flag: bool):
    user = user_opt or username
    pwd = pass_opt or password
    if not user or not pwd:
        raise click.UsageError("Нужно указать логин и пароль (позиционно или через -у/-p).")
    try:
        print("→ POST http://allta.devos.astralinux.ru/api/auth/login")
        auth_utils.login(login=user, password=pwd, verbose=True)
        click.secho("✓ Вход выполнен.", fg="green")
    except auth_utils.AuthError as e:
        click.secho(f"Ошибка входа: {e}", fg="red")
        sys.exit(1)
    _do_post_login_actions(git=git, tokens=tokens, boxes_flag=boxes_flag, releases_flag=releases_flag)

@cli.command("logout", help="Выйти")
def logout_cmd():
    try:
        auth_utils.logout(verbose=True)
    except auth_utils.AuthError as e:
        click.secho(f"Ошибка logout: {e}", fg="red")
        sys.exit(1)

@cli.command("python", help="Установить Python в домашний каталог и создать venv по умолчанию.")
@click.option("--venv", "with_shell", is_flag=False,
              help="После установки сразу открыть shell с активированным venv.")
def python_install_cmd(with_shell: bool):
    rc = install_python(activate_shell=with_shell)
    sys.exit(rc)

@cli.command("venv", help="Создать виртуальное окружение.")
@click.option("--path", "venv_path", type=click.Path(file_okay=False, dir_okay=True),
              help="Каталог для venv (должен существовать). По умолчанию: ./venv.")
def venv_cmd(venv_path: str | None):
    rc = create_venv(venv_path, enter_shell=True)
    sys.exit(rc)


if __name__ == "__main__":
    cli()
