"""Opaque cursor для keyset-пагинации list-эндпоинтов.

Курсор кодирует пару `(sort_key_value, id)` последней отданной строки.
На следующей странице WHERE-условие — `(sort_key, id) < (cursor_sort, cursor_id)`
для DESC-порядка. Двойной ключ нужен, чтобы устранить тай-брейк по дублирующимся
sort-значениям (created_at с millisecond-точностью на массовых вставках).

Формат: base64url(JSON({"k": "<sort_iso>", "i": "<id>"})) без padding'а.
Поле `k` — строка (ISO-8601 timestamp для created_at/discovered_at),
поле `i` — id строки. Короткие однобуквенные ключи — экономия в URL.

Невалидный / битый курсор → `InvalidCursorError`, endpoint оборачивает в 400.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime


class InvalidCursorError(ValueError):
    """Курсор не декодируется (битый base64 / JSON / отсутствуют поля)."""


@dataclass(frozen=True)
class Cursor:
    """Декодированная позиция: значение sort-ключа + id последней строки."""

    sort_value: str
    row_id: str


def encode_cursor(sort_value: datetime | str, row_id: str) -> str:
    """Собрать opaque-токен из последней строки страницы.

    `sort_value` для datetime сериализуется в ISO-8601 — это переживёт
    round-trip обратно в `datetime.fromisoformat`. Padding `=` срезаем —
    лишние символы в URL ни к чему, decode с `+ "==="` это переживёт.
    """
    if isinstance(sort_value, datetime):
        sort_str = sort_value.isoformat()
    else:
        sort_str = str(sort_value)
    payload = json.dumps({"k": sort_str, "i": row_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> Cursor:
    """Распаковать токен обратно в `Cursor`. Битый ввод → `InvalidCursorError`."""
    if not token:
        raise InvalidCursorError("cursor is empty")
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise InvalidCursorError("cursor is not valid base64url") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("cursor payload is not valid JSON") from exc
    if not isinstance(data, dict):
        raise InvalidCursorError("cursor payload is not an object")
    sort_value = data.get("k")
    row_id = data.get("i")
    if not isinstance(sort_value, str) or not isinstance(row_id, str):
        raise InvalidCursorError("cursor payload is missing 'k' or 'i'")
    return Cursor(sort_value=sort_value, row_id=row_id)


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


def normalize_limit(limit: int | None) -> int:
    """Зажать `limit` в [1, 500] с дефолтом 50.

    Endpoint-уровень валидирует диапазон через `Query(ge=1, le=500)` —
    верхняя граница согласована с FastAPI-валидатором, чтобы list-эндпоинты
    не получали молча урезанную страницу при limit > 200. Сервис-слой
    держит свою защиту на случай прямых вызовов из тестов/CLI.
    """
    if limit is None:
        return _DEFAULT_LIMIT
    if limit < 1:
        return 1
    if limit > _MAX_LIMIT:
        return _MAX_LIMIT
    return limit


def parse_cursor_datetime(value: str) -> datetime:
    """Преобразовать `Cursor.sort_value` обратно в `datetime`.

    Для repo-слоя, который сравнивает по timestamp-колонке. ISO-формат
    с TZ — стандарт; naive строки тоже принимаем (исторически могло
    отдаваться без TZ), считая их UTC.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidCursorError("cursor sort value is not a valid datetime") from exc
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
