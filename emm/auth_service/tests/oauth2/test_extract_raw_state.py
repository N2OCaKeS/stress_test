"""Юнит-тест `_extract_raw_state` — raw-байты `state` из query-string.

Хелпер вытаскивает значение `state=` из сырой query без URL-decode'а;
оауторайз echo'ит эти байты в callback'е 1:1, чтобы CSRF-сравнение клиента
шло byte-for-byte. Любой decode/encode-раунд в этом пути ломает SDK,
которые хранят state как opaque ASCII.
"""

import pytest

from src.api.v1.endpoints.oauth2 import _extract_raw_state


@pytest.mark.parametrize(
    "raw_query,expected",
    [
        # Базовые случаи.
        ("state=abc", "abc"),
        ("client_id=cli_1&state=xyz&redirect_uri=u", "xyz"),
        ("state=", ""),
        # Никакого decode'а: %-литералы возвращаем как есть.
        ("state=a%20b", "a%20b"),
        ("state=a%2520b", "a%2520b"),
        ("state=a%2Bb", "a%2Bb"),
        ("state=foo+bar", "foo+bar"),  # `+` сохраняется literal'ом.
        # Несколько параметров до и после.
        ("a=1&state=v&b=2", "v"),
        # Первое вхождение при дубликатах.
        ("state=first&state=second", "first"),
        # Параметр отсутствует.
        ("", None),
        ("client_id=cli_1", None),
        # Префикс совпадает, но не сам ключ — не должен матчиться.
        ("stateful=yes", None),
        ("statex=v", None),
    ],
)
def test_extract_raw_state(raw_query, expected):
    assert _extract_raw_state(raw_query) == expected
