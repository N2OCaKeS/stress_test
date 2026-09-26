"""Эталон паритета с allta_app.

`legacy.py` — порт построения легаси-`dates` и команд запуска чистыми
функциями, без импорта модулей `allta_app_full` (они при импорте ходят в
сеть и читают файлы). Golden-тесты (`tests/test_legacy_golden_parity.py`)
сравнивают с ним то, что собирает testing_service.
"""

from tests.legacy_parity.legacy import (
    LegacyCredentials,
    argv_pairs,
    legacy_dates,
    legacy_dates_written,
    legacy_launch_commands,
    legacy_parent_page,
    legacy_run,
    short_name_of,
)

__all__ = [
    "LegacyCredentials",
    "argv_pairs",
    "legacy_dates",
    "legacy_dates_written",
    "legacy_launch_commands",
    "legacy_parent_page",
    "legacy_run",
    "short_name_of",
]
