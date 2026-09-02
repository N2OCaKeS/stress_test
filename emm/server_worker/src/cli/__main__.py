"""Entry-point для `python -m src.cli`.

Argparse-дispatcher над подкомандами. Сейчас единственная команда —
`outbox-reattempt`, со временем сюда добавятся другие операторские
ручки.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.cli import outbox


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="server_worker operator CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    reattempt = sub.add_parser(
        "outbox-reattempt",
        help="вернуть audit_outbox-row из DLQ обратно в очередь publisher'а",
    )
    reattempt.add_argument(
        "row_id",
        type=int,
        help="audit_outbox.id строки, которую нужно повторить",
    )
    reattempt.add_argument(
        "--reason",
        default=None,
        help="произвольный текст для audit-события (зачем re-attempt).",
    )
    reattempt.add_argument(
        "--actor-id",
        default=None,
        help="оператор, инициировавший re-attempt (для audit-трейла).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "outbox-reattempt":
        ok = asyncio.run(
            outbox.cmd_outbox_reattempt(
                row_id=args.row_id,
                reason=args.reason,
                actor_id=args.actor_id,
            )
        )
        return 0 if ok else 1

    # argparse с `required=True` сюда не пустит, но на всякий случай.
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
