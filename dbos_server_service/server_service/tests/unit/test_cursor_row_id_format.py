"""Валидация формата `row_id` в decoded-курсоре.

До этого фикса decode_cursor проверял только isinstance(row_id, str). Любой
junk-row_id (megabyte string, ascii-spam, специальные символы) проходил decode
и улетал в WHERE-условие repo-слоя, где давал пустую страницу — клиент видел
end-of-stream вместо честной 400 INVALID_CURSOR.

После — row_id ограничен регуляркой `^[A-Za-z0-9_\\-]{1,64}$` (наши id'шники
из `src/utils/ids.py` базируются на base32hex + ASCII-префикс, длина ~16
символов).
"""

from __future__ import annotations

import base64
import json

import pytest

from src.utils.cursor import (
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)


def _make_token(row_id: str, sort_value: str = "2026-05-30T12:00:00") -> str:
    """Принудительно собрать курсор с заданным row_id, минуя encode_cursor.

    `encode_cursor` сам по себе не валидирует input — кладёт любой `row_id` в
    JSON. Нам нужно покрыть случаи, когда курсор пришёл из внешнего источника
    (manual-crafted / повреждённый клиент / атака).
    """
    payload = json.dumps({"k": sort_value, "i": row_id}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


class TestRowIdFormat:
    def test_good_id_passes(self):
        """Стандартный наш id (префикс + base32hex) проходит."""
        token = _make_token("srv_abc123def456")
        cur = decode_cursor(token)
        assert cur.row_id == "srv_abc123def456"

    def test_hyphenated_id_passes(self):
        """Tier-2: id'шники с дефисами (uuid-подобные) — допустимы."""
        token = _make_token("srv-abc-123")
        cur = decode_cursor(token)
        assert cur.row_id == "srv-abc-123"

    def test_empty_row_id_rejected(self):
        """row_id="" — мин. длина 1."""
        token = _make_token("")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_too_long_row_id_rejected(self):
        """row_id длиннее 64 символов → InvalidCursorError, не пустая страница."""
        too_long = "a" * 65
        token = _make_token(too_long)
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_64_chars_is_max_accepted(self):
        """Граница включающая — ровно 64 символа допустимо."""
        edge = "a" * 64
        token = _make_token(edge)
        cur = decode_cursor(token)
        assert cur.row_id == edge

    def test_unicode_rejected(self):
        """Не-ASCII символы — отбрасываются."""
        token = _make_token("srv_АБВ")
        with pytest.raises(InvalidCursorError, match="row_id format invalid"):
            decode_cursor(token)

    def test_sql_injection_chars_rejected(self):
        """Спец-символы (одинарная кавычка, точка с запятой) — отбрасываются."""
        for junk in ["srv'1", "id; DROP TABLE", "srv\x00abc", "srv abc"]:
            token = _make_token(junk)
            with pytest.raises(InvalidCursorError, match="row_id format invalid"):
                decode_cursor(token)

    def test_round_trip_real_id_succeeds(self):
        """encode_cursor → decode_cursor через реальный путь — на нашем id."""
        from datetime import datetime, timezone

        ts = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
        token = encode_cursor(ts, "srv_zk5m6n7p8q")
        cur = decode_cursor(token)
        assert cur.row_id == "srv_zk5m6n7p8q"
