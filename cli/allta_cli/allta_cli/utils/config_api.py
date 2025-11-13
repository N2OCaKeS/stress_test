from __future__ import annotations
import json
from typing import Any, Optional, Union, Dict
from pathlib import Path

import requests

from allta_cli.utils import ui
from allta_cli.utils.auth import load_token
from allta_cli.utils.config import CONFIG_API_BASE


class ConfigApiError(RuntimeError):
    """Общая ошибка обращения к config-API."""


class TokenKeyNotFound(ConfigApiError):
    """Запрошенный ключ токена отсутствует в tokens.json."""


def _auth_headers() -> Dict[str, str]:
    token = load_token(verbose=False)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def tokens(tokens_type: Optional[str] = None) -> Union[str, Dict[str, str]]:
    """
    Возвращает либо весь tokens.json, либо отдельный ключ.
    Использует единый стиль логирования и обработки ошибок.
    """
    url = f"{CONFIG_API_BASE}/config/tokens"
    ui.http(f"GET {url}")
    try:
        r = requests.get(url, headers=_auth_headers(), timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить tokens.json: {e}") from e

    try:
        data = r.json()
    except ValueError:
        raise ConfigApiError("Сервер вернул не-JSON при запросе /config/tokens.")

    if not isinstance(data, dict):
        raise ConfigApiError("Неверный формат ответа для tokens.json: ожидается объект JSON.")

    if tokens_type is None:
        ui.ok("tokens.json успешно получен.")
        return data

    if tokens_type in data and isinstance(data[tokens_type], str):
        ui.ok(f"Токен '{tokens_type}' успешно получен.")
        return data[tokens_type]

    raise TokenKeyNotFound(f"В tokens.json отсутствует ключ '{tokens_type}'.")


def files(filename: str) -> Any:
    """
    Получает JSON-файл из config-API и возвращает его содержимое как dict.
    Все ошибки — в едином стиле через ConfigApiError.
    """
    if not filename:
        raise ConfigApiError("Не указано имя файла (filename).")

    url = f"{CONFIG_API_BASE}/config/files/{filename}"
    ui.http(f"GET {url}")

    try:
        r = requests.get(url, headers=_auth_headers(), timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ConfigApiError(f"Не удалось получить файл '{filename}': {e}") from e

    try:
        js = r.json()
    except ValueError:
        snippet = (r.text or "")[:200]
        raise ConfigApiError(
            f"Файл '{filename}' не является корректным JSON. "
            f"Фрагмент ответа: {snippet!r}"
        )

    ui.ok(f"Файл '{filename}' успешно получен.")
    return js
