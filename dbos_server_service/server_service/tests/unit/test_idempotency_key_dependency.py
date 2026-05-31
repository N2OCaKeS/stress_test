"""Unit-тесты `dependencies/idempotency.read_idempotency_key`.

Изолируют чистую логику header→str|None+валидация-длины от FastAPI и
БД. Endpoint-уровневые тесты сидят в
`tests/test_worker_task_dispatch_endpoints.py::TestIdempotencyKeyLength`.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import BadRequestError
from src.dependencies.idempotency import (
    IDEMPOTENCY_KEY_MAX_LEN,
    read_idempotency_key,
)


class _FakeRequest:
    """Дешёвый stub: helper читает только `.headers.get(...)`."""

    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {}


def test_max_len_covers_worker_column_with_server_id_suffix():
    """Sanity: лимит + ":" + 36-char server_id ("srv_" + uuid4.hex) влезает в 128."""
    # 87 + 1 + 36 = 124 ≤ 128, остаётся запас на возможный рост server.id.
    assert IDEMPOTENCY_KEY_MAX_LEN + 1 + 36 <= 128


def test_missing_header_returns_none():
    assert read_idempotency_key(_FakeRequest()) is None


def test_empty_header_returns_none():
    """Пустая строка эквивалентна отсутствию (сохранили старое `... or None`)."""
    assert read_idempotency_key(_FakeRequest({"Idempotency-Key": ""})) is None


def test_normal_uuid_key_passes():
    key = "550e8400-e29b-41d4-a716-446655440000"  # 36 chars
    assert read_idempotency_key(_FakeRequest({"Idempotency-Key": key})) == key


def test_short_key_passes():
    assert read_idempotency_key(_FakeRequest({"Idempotency-Key": "abc"})) == "abc"


def test_boundary_key_at_max_len_passes():
    key = "k" * IDEMPOTENCY_KEY_MAX_LEN
    assert read_idempotency_key(_FakeRequest({"Idempotency-Key": key})) == key


def test_oversized_key_raises_bad_request():
    key = "x" * (IDEMPOTENCY_KEY_MAX_LEN + 1)
    with pytest.raises(BadRequestError) as exc:
        read_idempotency_key(_FakeRequest({"Idempotency-Key": key}))
    err = exc.value
    assert err.error_code == "IDEMPOTENCY_KEY_TOO_LONG"
    assert err.http_status == 400
    assert err.details["max_length"] == IDEMPOTENCY_KEY_MAX_LEN
    assert err.details["got"] == IDEMPOTENCY_KEY_MAX_LEN + 1


def test_very_long_key_raises_bad_request():
    """Реальный сценарий эксплуатации: 200-char ключ через header."""
    key = "z" * 200
    with pytest.raises(BadRequestError) as exc:
        read_idempotency_key(_FakeRequest({"Idempotency-Key": key}))
    assert exc.value.error_code == "IDEMPOTENCY_KEY_TOO_LONG"
    assert exc.value.details["got"] == 200
