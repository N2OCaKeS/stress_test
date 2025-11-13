from __future__ import annotations
import json
from typing import Optional

from allta_cli.utils import ui
from allta_cli.utils.config_api import tokens as fetch_tokens, ConfigApiError, TokenKeyNotFound
from allta_cli.utils.auth import AuthError, TokenExpiredError, NotAuthenticatedError


def tokens_cmd(token_type: Optional[str] = None) -> int:
    """
    Печатает tokens.json:
      - если token_type None — выводит весь объект с разделителями;
      - если token_type задан — печатает только значение этого ключа.
    Возвращает код завершения (0/1).
    """
    try:
        data = fetch_tokens(token_type)
    except (TokenKeyNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if token_type:
        with ui.section("tokens.json • один ключ"):
            ui.echo(f"Ключ: {token_type}")
            ui.echo("Значение:")
            # data здесь строка токена
            print(data)
        return 0

    ui.header("tokens.json • все токены (начало)")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    ui.footer("tokens.json • все токены (конец)")
    return 0
