from __future__ import annotations

import sys

import click

from allta_cli.cli import COMMAND_SHORTCUTS, cli, run_login_shortcut_argv


# Commands that never talk to the allta API and therefore don't need the TLS
# bootstrap (which makes a network call to fetch the API cert). Listed by their
# canonical click names — shortcuts are expanded before this check.
_NO_TLS_COMMANDS = frozenset({"kernel", "modeswitch", "mc", "local", "python", "venv"})


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


def _skip_tls_bootstrap(args: list[str]) -> bool:
    if not args:
        return True
    if any(a in ("-h", "--help", "--version") for a in args):
        return True
    for arg in args:
        if not arg.startswith("-"):
            return arg in _NO_TLS_COMMANDS
    return False


def main(argv: list[str] | None = None):
    args = _expand_shortcuts(list(sys.argv[1:] if argv is None else argv))
    if not _skip_tls_bootstrap(args):
        from allta_cli.utils.tls_bootstrap import ensure_api_tls_trust

        ensure_api_tls_trust()
    try:
        if _should_handle_login_shortcut(args):
            raise SystemExit(run_login_shortcut_argv(args))
        cli.main(args=args, prog_name="allta", standalone_mode=False)
    except click.ClickException as e:
        e.show()
        raise SystemExit(e.exit_code)


if __name__ == "__main__":
    main()
