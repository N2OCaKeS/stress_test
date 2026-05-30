"""Unit-тесты cursor utility (encode/decode/edge cases)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.utils.cursor import (
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
    normalize_limit,
    parse_cursor_datetime,
)


class TestEncodeDecode:
    def test_round_trip_datetime(self):
        ts = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
        token = encode_cursor(ts, "srv_abc123")
        cur = decode_cursor(token)
        assert cur.row_id == "srv_abc123"
        assert parse_cursor_datetime(cur.sort_value) == ts

    def test_round_trip_string_sort_value(self):
        token = encode_cursor("2026-05-30T12:00:00", "x")
        cur = decode_cursor(token)
        assert cur.sort_value == "2026-05-30T12:00:00"
        assert cur.row_id == "x"

    def test_token_is_url_safe(self):
        ts = datetime(2026, 5, 30, tzinfo=timezone.utc)
        token = encode_cursor(ts, "srv_/+=test")
        # Никаких '+' и '/' — urlsafe base64. Padding '=' срезан.
        assert "+" not in token
        assert "/" not in token
        assert not token.endswith("=")

    def test_empty_token_rejected(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor("")

    def test_garbage_base64_rejected(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor("not!base64!at!all")

    def test_valid_base64_not_json_rejected(self):
        # "hi" в base64 — валидно, но не JSON.
        with pytest.raises(InvalidCursorError):
            decode_cursor("aGk")

    def test_json_not_object_rejected(self):
        import base64
        token = base64.urlsafe_b64encode(b'[1, 2]').decode().rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token)

    def test_json_missing_keys_rejected(self):
        import base64
        token = base64.urlsafe_b64encode(b'{"k": "x"}').decode().rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(token)

    def test_parse_naive_datetime_assumes_utc(self):
        dt = parse_cursor_datetime("2026-05-30T12:00:00")
        assert dt.tzinfo is not None

    def test_parse_invalid_datetime_raises(self):
        with pytest.raises(InvalidCursorError):
            parse_cursor_datetime("not-a-date")


class TestNormalizeLimit:
    def test_none_returns_default(self):
        assert normalize_limit(None) == 50

    def test_below_min_clamped(self):
        assert normalize_limit(0) == 1
        assert normalize_limit(-5) == 1

    def test_above_max_clamped(self):
        assert normalize_limit(1000) == 500

    def test_in_range_passes(self):
        assert normalize_limit(75) == 75

    def test_max_matches_endpoint_query_cap(self):
        """Service-cap синхронен с endpoint'ным `Query(le=500)` — limit=500
        не должен молча урезаться до 200.
        """
        assert normalize_limit(500) == 500
        assert normalize_limit(499) == 499
