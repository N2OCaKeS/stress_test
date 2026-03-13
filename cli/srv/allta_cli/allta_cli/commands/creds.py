from __future__ import annotations

import json

from allta_cli.utils import ui
from allta_cli.utils.auth import AuthError, NotAuthenticatedError, TokenExpiredError
from allta_cli.utils.config_api import (
    ConfigApiError,
    ServiceCredential,
    ServiceCredentialNotFound,
    delete_service_credential as delete_credential,
    get_service_credential as fetch_credential,
    list_service_credentials as fetch_credentials,
    update_service_credential as patch_credential,
    upsert_service_credential as save_credential,
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


def _print_table(items: list[ServiceCredential]) -> None:
    rows: list[list[str]] = []
    for item in sorted(items, key=lambda x: x["service_name"].lower()):
        rows.append(
            [
                item["service_name"],
                item["username"],
                item["password"],
                item["updated_by"] or "",
                _format_dt(item["updated_at"]),
            ]
        )
    ui.table(headers=["Service", "Login", "Password", "Updated By", "Updated At"], rows=rows)


def list_credentials_cmd(*, raw: bool = False) -> int:
    try:
        items = fetch_credentials()
    except (ServiceCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if raw:
        _dump_raw("config/credentials", items)
        return 0

    if not items:
        ui.warn("Список сервисных кредов пуст.")
        return 0

    _print_table(items)
    return 0


def get_credential_cmd(service_name: str, *, raw: bool = False) -> int:
    try:
        item = fetch_credential(service_name)
    except (ServiceCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if raw:
        _dump_raw(f"config/credentials/{item['service_name']}", item)
        return 0

    _print_table([item])
    return 0


def upsert_credential_cmd(service_name: str, username: str, password: str, *, raw: bool = False) -> int:
    try:
        item = save_credential(service_name, username, password)
    except (ServiceCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if raw:
        _dump_raw(f"config/credentials/{item['service_name']}", item)
        return 0

    _print_table([item])
    return 0


def update_credential_cmd(
    service_name: str,
    *,
    username: str | None = None,
    password: str | None = None,
    raw: bool = False,
) -> int:
    try:
        item = patch_credential(service_name, username=username, password=password)
    except (ServiceCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    if raw:
        _dump_raw(f"config/credentials/{item['service_name']}", item)
        return 0

    _print_table([item])
    return 0


def delete_credential_cmd(service_name: str) -> int:
    try:
        delete_credential(service_name)
    except (ServiceCredentialNotFound, NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1
    return 0
