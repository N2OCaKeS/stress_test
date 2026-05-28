"""Unit-тесты схем `src/schemas/internal.py` (Pydantic-валидация).

Проверяем граничные значения полей, которые приходят от worker_bot —
длины строк, разрешённые символы. Schema-level guard'ы держим узкими,
чтобы скомпрометированный worker_bot не мог раздуть payload или загрязнить
глобальный os-каталог мусорным именем.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.internal import InventoryCallbackRequest


_BASE = {
    "hostname": "srv-01",
    "kernel": "5.10.0-astra-amd64",
    "cpu_brand": "Intel",
    "cpu_model": "Xeon",
    "cpu_cores": 4,
    "cpu_threads": 8,
    "cpu_frequency_ghz": 2.4,
    "os_version": "Astra Linux SE 1.7",
    "disks": [],
}


class TestInventoryCallbackLspciLimit:
    def test_lspci_within_limit_accepted(self):
        m = InventoryCallbackRequest(**{**_BASE, "lspci": "x" * 8192})
        assert m.lspci is not None
        assert len(m.lspci) == 8192

    def test_lspci_over_limit_rejected(self):
        with pytest.raises(ValidationError) as exc:
            InventoryCallbackRequest(**{**_BASE, "lspci": "x" * 8193})
        assert "lspci" in str(exc.value)

    def test_lspci_optional(self):
        m = InventoryCallbackRequest(**_BASE)
        assert m.lspci is None


class TestInventoryCallbackOsVersion:
    def test_os_version_simple_name_accepted(self):
        m = InventoryCallbackRequest(**{**_BASE, "os_version": "Astra Linux SE 1.7"})
        assert m.os_version == "Astra Linux SE 1.7"

    def test_os_version_with_dots_dashes_underscores_accepted(self):
        m = InventoryCallbackRequest(
            **{**_BASE, "os_version": "Astra.Linux_SE-1.7.4"}
        )
        assert m.os_version == "Astra.Linux_SE-1.7.4"

    def test_os_version_with_shell_meta_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "os_version": "Astra; rm -rf /"})

    def test_os_version_with_unicode_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "os_version": "Астра 1.7"})

    def test_os_version_over_max_length_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "os_version": "a" * 129})

    def test_os_version_empty_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "os_version": ""})
