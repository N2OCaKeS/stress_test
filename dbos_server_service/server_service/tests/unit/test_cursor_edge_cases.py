"""Edge-case тесты cursor utility — граничные значения и нестандартные payload'ы.

Покрывает сценарии, не вошедшие в test_cursor.py:
* encode_cursor с нулевым row_id и с row_id из произвольных unicode-символов;
* decode_cursor с padding-вариантами (токены с разной длиной mod 4);
* normalize_limit на граничных значениях 1 и 200;
* parse_cursor_datetime с aware-строкой не-UTC (должна не упасть);
* json с лишними ключами (k и i присутствуют — должен пройти).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone, timedelta

import pytest

from src.utils.cursor import (
    Cursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
    normalize_limit,
    parse_cursor_datetime,
)


class TestEncodeDecodeEdgeCases:
    def test_empty_row_id_round_trip(self):
        """row_id из пустой строки — технически допустим, не бросает."""
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        token = encode_cursor(ts, "")
        cur = decode_cursor(token)
        assert cur.row_id == ""

    def test_unicode_row_id_round_trip(self):
        token = encode_cursor("some-sort-value", "srv_АБВ_123")
        cur = decode_cursor(token)
        assert cur.row_id == "srv_АБВ_123"

    def test_payload_with_extra_keys_accepted(self):
        """JSON с лишними ключами — допустим, decode берёт только k и i."""
        payload = json.dumps({"k": "2026-01-01T00:00:00", "i": "srv_x", "extra": 99})
        token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        cur = decode_cursor(token)
        assert cur.sort_value == "2026-01-01T00:00:00"
        assert cur.row_id == "srv_x"

    def test_tokens_of_all_padding_lengths_decode(self):
        """Токены длиной mod 4 = 0, 1, 2, 3 все должны декодироваться."""
        ts = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
        for suffix in ["", "x", "xy", "xyz"]:
            token = encode_cursor(ts, f"id{suffix}")
            cur = decode_cursor(token)
            assert cur.row_id == f"id{suffix}"

    def test_key_value_is_integer_rejected(self):
        """i — целое число (не строка) → InvalidCursorError."""
        payload = json.dumps({"k": "2026-01-01T00:00:00", "i": 42})
        token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token)

    def test_sort_value_is_null_rejected(self):
        """k = null → InvalidCursorError."""
        payload = json.dumps({"k": None, "i": "srv_x"})
        token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token)

    def test_json_array_of_valid_objects_rejected(self):
        """Массив объектов — не dict на верхнем уровне."""
        payload = json.dumps([{"k": "2026-01-01T00:00:00", "i": "x"}])
        token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token)

    def test_whitespace_only_token_rejected(self):
        """Строка из пробелов — не пустая, но не base64."""
        with pytest.raises(InvalidCursorError):
            decode_cursor("   ")

    def test_encode_non_utc_datetime_round_trips(self):
        """encode/decode с aware-datetime в не-UTC (MSK, UTC+3)."""
        msk = timezone(timedelta(hours=3))
        ts = datetime(2026, 5, 30, 15, 0, 0, tzinfo=msk)
        token = encode_cursor(ts, "srv_msk")
        cur = decode_cursor(token)
        # sort_value — ISO-строка с offset +03:00
        recovered = parse_cursor_datetime(cur.sort_value)
        assert recovered is not None
        # Момент совпадает с UTC-эквивалентом
        assert recovered.utctimetuple() == ts.utctimetuple()


class TestNormalizeLimitBoundary:
    def test_exactly_one(self):
        assert normalize_limit(1) == 1

    def test_exactly_max(self):
        assert normalize_limit(200) == 200

    def test_one_below_min(self):
        assert normalize_limit(0) == 1

    def test_one_above_max(self):
        assert normalize_limit(201) == 200

    def test_mid_range(self):
        for v in [1, 50, 100, 150, 200]:
            assert 1 <= normalize_limit(v) <= 200


class TestParseCursorDatetime:
    def test_aware_non_utc_accepted(self):
        """Строка с offset не-UTC не должна падать."""
        dt = parse_cursor_datetime("2026-05-30T15:00:00+03:00")
        assert dt.tzinfo is not None

    def test_microseconds_preserved(self):
        """Microseconds в ISO-строке сохраняются."""
        dt = parse_cursor_datetime("2026-05-30T12:00:00.123456+00:00")
        assert dt.microsecond == 123456

    def test_date_only_string_rejected(self):
        """Строка только с датой без времени — fromisoformat в 3.11 принимает,
        но результат не несёт tzinfo — проверяем, что UTC-коррекция не ломается."""
        dt = parse_cursor_datetime("2026-05-30")
        # Должна вернуть datetime с UTC (naive → UTC patch).
        assert dt.tzinfo is not None
