"""normalize_os_version_name — компактные легаси-имена build'ов в dotted-формат."""

import pytest

from src.core.known_os import normalize_os_version_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("175", "1.7.5"),
        ("1710rc52", "1.7.10.52"),
        ("175UU1rc7", "1.7.5.UU.1.7"),
        ("1.7.11.42", "1.7.11.42"),
        ("debian-stable", "debian-stable"),
        ("", ""),
    ],
)
def test_normalize_os_version_name(raw: str, expected: str) -> None:
    assert normalize_os_version_name(raw) == expected


def test_normalize_os_version_name_strips_whitespace() -> None:
    assert normalize_os_version_name("  175  ") == "1.7.5"
