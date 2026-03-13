from __future__ import annotations

import json
from typing import Optional

from allta_cli.utils import ui
from allta_cli.utils.auth import AuthError, NotAuthenticatedError, TokenExpiredError
from allta_cli.utils.config_api import (
    ConfigApiError,
    TokenCredential,
    TokenCredentialNotFound,
    TokenKeyNotFound,
    delete_token_credential as delete_token,
    get_token_credential as fetch_token,
    list_token_credentials as fetch_token_credentials,
    tokens as fetch_tokens,
    update_token_credential as patch_token,
    upsert_token_credential as save_token,
)


def _format_dt(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = text.replace("T", " ")
    if "." in text:
        text = text.split(".", 1)[0]
    return text.rstrip("Z")


def _dump_raw(title: str, data: object) -> None:
    ui.header(f"{title} (начало)")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    ui.footer(f"{title} (конец)")


def _print_tokens_json(data: dict[str, str]) -> None:
    rows: list[list[str]] = []
    for key in sorted(data.keys(), key=str.lower):
        rows.append([key, str(data[key])])
    ui.table(headers=["Token Key", "Token"], rows=rows)


def _print_credential_table(items: list[TokenCredential]) -> None:
    rows: list[list[str]] = []
    for item in sorted(items, key=lambda x: x["token_key"].lower()):
        rows.append(
            [
                item["token_key"],
                item["token"],
                item["updated_by"] or "",
                _format_dt(item["updated_at"]),
            ]
        )
    ui.table(headers=["Token Key", "Token", "Updated By", "Updated At"], rows=rows)


def tokens_cmd(token_type: Optional[str] = None) -> int:
    """
    Базовый вывод tokens.json:
      - если token_type не задан — таблица со всеми токенами;
      - если token_type задан — вывод только значения ключа.
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
            print(data)
        return 0

    if not isinstance(data, dict):
        ui.err("Ошибка: некорректный формат tokens.json.")
        return 1

    if not data:
        ui.warn("Список токенов пуст.")
        return 0

    _print_tokens_json(data)
    return 0


def list_token_credentials_cmd(*, raw: bool = False) -> int:
    try:
        items = fetch_token_credentials()
    except (TokenCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if raw:
        _dump_raw("config/tokens/details", items)
        return 0

    if not items:
        ui.warn("Список токенов пуст.")
        return 0

    _print_credential_table(items)
    return 0


def get_token_credential_cmd(token_key: str, *, raw: bool = False) -> int:
    try:
        item = fetch_token(token_key)
    except (TokenCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if raw:
        _dump_raw(f"config/tokens/details/{item['token_key']}", item)
        return 0

    _print_credential_table([item])
    return 0


def upsert_token_credential_cmd(token_key: str, token: str, *, raw: bool = False) -> int:
    try:
        item = save_token(token_key, token)
    except (TokenCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if raw:
        _dump_raw(f"config/tokens/details/{item['token_key']}", item)
        return 0

    _print_credential_table([item])
    return 0


def update_token_credential_cmd(token_key: str, token: str, *, raw: bool = False) -> int:
    try:
        item = patch_token(token_key, token)
    except (TokenCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if raw:
        _dump_raw(f"config/tokens/details/{item['token_key']}", item)
        return 0

    _print_credential_table([item])
    return 0


def delete_token_credential_cmd(token_key: str) -> int:
    try:
        delete_token(token_key)
    except (TokenCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1
    return 0
