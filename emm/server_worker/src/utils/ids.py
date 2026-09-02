"""Генерация ID с префиксом — uuid4 hex плюс short prefix."""

import uuid


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def task_id() -> str:
    """Сгенерировать новый task ID. Префикс `tsk_`, дальше uuid4-hex."""
    return _new_id("tsk_")
