from __future__ import annotations

import sys

import click

from allta_cli.cli import COMMAND_SHORTCUTS, cli, run_login_shortcut_argv
from allta_cli.utils.tls_bootstrap import ensure_api_tls_trust


def _should_handle_login_shortcut(args: list[str]) -> bool:
    if not args:
        return False
    if not any(arg in ("-l", "--login") for arg in args):
        return False
    if any(arg in ("-h", "--help", "--version") for arg in args):
        return False
    return True


def _expand_shortcuts(args: list[str]) -> list[str]:
    if not args:
        return args
    first = args[0]
    if first.startswith("-"):
        return args
    target = COMMAND_SHORTCUTS.get(first)
    if not target:
        return args
    return [target, *args[1:]]


def main(argv: list[str] | None = None):
    ensure_api_tls_trust()
    args = _expand_shortcuts(list(sys.argv[1:] if argv is None else argv))
    try:
        if _should_handle_login_shortcut(args):
            raise SystemExit(run_login_shortcut_argv(args))
        cli.main(args=args, prog_name="allta", standalone_mode=False)
    except click.ClickException as e:
        e.show()
        raise SystemExit(e.exit_code)


if __name__ == "__main__":
    main()
