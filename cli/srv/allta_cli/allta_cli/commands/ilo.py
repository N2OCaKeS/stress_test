from __future__ import annotations

import json
import re

from allta_cli.utils import ui
from allta_cli.utils.auth import AuthError, NotAuthenticatedError, TokenExpiredError
from allta_cli.utils.config_api import ConfigApiError, IloEntry, ilo as fetch_ilo


def _dump_raw(data: dict[str, IloEntry]) -> None:
    ui.header("server/ilo (начало)")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    ui.footer("server/ilo (конец)")


def _stand_sort_key(name: str) -> tuple[int, str]:
    match = re.fullmatch(r"stand(\d+)", name, flags=re.IGNORECASE)
    if match:
        return (int(match.group(1)), name.lower())
    return (10**9, name.lower())


def _resolve_stand(data: dict[str, IloEntry], stand_query: str) -> str | None:
    query = stand_query.strip()
    if not query:
        return None

    if query in data:
        return query

    lowered = query.lower()
    for stand in data:
        if stand.lower() == lowered:
            return stand

    if query.isdigit():
        target = f"stand{query}"
        for stand in data:
            if stand.lower() == target.lower():
                return stand

    return None


def _to_url(ip: str) -> str:
    return f"https://{ip}/"


def ilo_cmd(*, stand_query: str | None = None, raw: bool = False) -> int:
    try:
        data = fetch_ilo()
    except (NotAuthenticatedError, TokenExpiredError, AuthError, ConfigApiError) as e:
        ui.err(f"Ошибка: {e}")
        return 1

    selected = data
    if stand_query:
        stand_name = _resolve_stand(data, stand_query)
        if not stand_name:
            available = ", ".join(sorted(data.keys(), key=_stand_sort_key))
            ui.err(f"Стенд '{stand_query}' не найден.")
            if available:
                ui.echo(f"Доступные стенды: {available}")
            return 1
        selected = {stand_name: data[stand_name]}

    if raw:
        _dump_raw(selected)
        return 0

    rows: list[list[str]] = []
    for stand in sorted(selected, key=_stand_sort_key):
        item = selected[stand]
        rows.append([stand, _to_url(item["ip"]), item["username"], item["password"]])

    if not rows:
        ui.warn("Список iLO-кредов пуст.")
        return 0

    ui.table(headers=["Stand", "URL", "Login", "Password"], rows=rows)
    return 0
