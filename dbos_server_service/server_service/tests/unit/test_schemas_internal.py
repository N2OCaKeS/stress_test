"""Unit-тесты схем `src/schemas/internal.py` (Pydantic-валидация).

Проверяем граничные значения полей, которые приходят от worker_bot —
длины строк, разрешённые символы. Schema-level guard'ы держим узкими,
чтобы скомпрометированный worker_bot не мог раздуть payload или загрязнить
глобальный os-каталог мусорным именем.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.internal import (
    InventoryCallbackRequest,
    IpmiCredentialsRotatedRequest,
    PasswordRotateRequest,
    UsersInventoryCallbackRequest,
)


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

    def test_os_version_pretty_name_with_slash_and_parens_accepted(self):
        # Реальный PRETTY_NAME Debian/Astra несёт `/` и скобки — раньше схема
        # их резала и инвентаризация падала 422.
        value = "Debian GNU/Linux 13 (trixie)"
        m = InventoryCallbackRequest(**{**_BASE, "os_version": value})
        assert m.os_version == value

    def test_os_version_cyrillic_release_name_accepted(self):
        # Astra SE отдаёт уровень защищённости в PRETTY_NAME кириллицей: `(Орёл)`.
        value = "Astra Linux SE 1.7 (Орёл)"
        m = InventoryCallbackRequest(**{**_BASE, "os_version": value})
        assert m.os_version == value

    def test_os_version_with_pipe_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "os_version": "Astra|cat"})

    def test_os_version_with_dollar_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "os_version": "Astra$(whoami)"})

    def test_os_version_over_max_length_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "os_version": "a" * 129})

    def test_os_version_empty_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "os_version": ""})


# ── Парольная политика для PasswordRotateRequest ────────────────────────────


class TestPasswordRotateRequestPolicy:
    def test_compliant_password_accepted(self):
        m = PasswordRotateRequest(password="Strong1Password")
        assert m.password == "Strong1Password"

    def test_short_password_rejected(self):
        with pytest.raises(ValidationError):
            PasswordRotateRequest(password="A1b")

    def test_password_without_digit_rejected(self):
        with pytest.raises(ValidationError):
            PasswordRotateRequest(password="OnlyLettersHere")

    def test_password_without_letter_rejected(self):
        with pytest.raises(ValidationError):
            PasswordRotateRequest(password="12345678")

    def test_token_urlsafe_like_passes_policy(self):
        # `secrets.token_urlsafe(32)` всегда содержит и буквы, и цифры —
        # тут просто проверяем, что типичный auto-gen worker'а проходит.
        m = PasswordRotateRequest(password="abcDEF1234567890xyzABCDEFGHIJK")
        assert len(m.password) >= 8


class TestIpmiCredentialsRotatedRequestPolicy:
    _ROTATED = "2026-01-01T00:00:00+00:00"
    _VERIFIED = "2026-01-01T00:00:00+00:00"

    def test_compliant_password_accepted(self):
        m = IpmiCredentialsRotatedRequest(
            new_password="Strong1Password",
            rotated_at=self._ROTATED,
            verified_at=self._VERIFIED,
        )
        assert m.new_password == "Strong1Password"
        assert m.verified_at is not None

    def test_short_password_rejected(self):
        with pytest.raises(ValidationError):
            IpmiCredentialsRotatedRequest(
                new_password="A1b",
                rotated_at=self._ROTATED,
                verified_at=self._VERIFIED,
            )

    def test_password_without_digit_rejected(self):
        with pytest.raises(ValidationError):
            IpmiCredentialsRotatedRequest(
                new_password="OnlyLettersHere",
                rotated_at=self._ROTATED,
                verified_at=self._VERIFIED,
            )

    def test_password_without_letter_rejected(self):
        with pytest.raises(ValidationError):
            IpmiCredentialsRotatedRequest(
                new_password="12345678",
                rotated_at=self._ROTATED,
                verified_at=self._VERIFIED,
            )

    def test_missing_verified_at_rejected(self):
        """`verified_at` обязательное поле — отсутствие → ValidationError."""
        with pytest.raises(ValidationError):
            IpmiCredentialsRotatedRequest(
                new_password="Strong1Password",
                rotated_at=self._ROTATED,
            )

    def test_naive_verified_at_is_tz_normalized(self):
        """Naive datetime приходит как UTC (контракт internal-канала)."""
        m = IpmiCredentialsRotatedRequest(
            new_password="Strong1Password",
            rotated_at="2026-01-01T00:00:00",
            verified_at="2026-01-01T00:00:00",
        )
        assert m.verified_at.tzinfo is not None
        assert m.rotated_at.tzinfo is not None


# ── Лимит на размер inventory-списков ───────────────────────────────────────


_DISK_ITEM = {
    "name": "sda",
    "size_gb": 100,
    "is_system": False,
}


def _disk_with_name(idx: int) -> dict:
    item = dict(_DISK_ITEM)
    item["name"] = f"sd{idx}"
    return item


class TestInventoryDisksCap:
    def test_128_disks_accepted(self):
        disks = [_disk_with_name(i) for i in range(128)]
        m = InventoryCallbackRequest(**{**_BASE, "disks": disks})
        assert len(m.disks) == 128

    def test_129_disks_rejected(self):
        disks = [_disk_with_name(i) for i in range(129)]
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "disks": disks})


class TestInventorySystemDiskInvariant:
    def test_zero_system_disks_accepted(self):
        m = InventoryCallbackRequest(**{**_BASE, "disks": [
            {"name": "sda", "size_gb": 100, "is_system": False},
            {"name": "sdb", "size_gb": 200, "is_system": False},
        ]})
        assert sum(1 for d in m.disks if d.is_system) == 0

    def test_one_system_disk_accepted(self):
        m = InventoryCallbackRequest(**{**_BASE, "disks": [
            {"name": "sda", "size_gb": 100, "is_system": True},
            {"name": "sdb", "size_gb": 200, "is_system": False},
        ]})
        assert sum(1 for d in m.disks if d.is_system) == 1

    def test_two_system_disks_rejected(self):
        with pytest.raises(ValidationError) as exc:
            InventoryCallbackRequest(**{**_BASE, "disks": [
                {"name": "sda", "size_gb": 100, "is_system": True},
                {"name": "sdb", "size_gb": 200, "is_system": True},
            ]})
        assert "is_system" in str(exc.value)


class TestInventoryNetworkMemoryDisks:
    def test_new_fields_default_absent(self):
        # Старый воркер не шлёт ram/network/used — back-compat: дефолты.
        m = InventoryCallbackRequest(**_BASE)
        assert m.ram_total_mb is None
        assert m.network_interfaces == []

    def test_ram_and_interfaces_accepted(self):
        m = InventoryCallbackRequest(**{
            **_BASE,
            "ram_total_mb": 16384,
            "network_interfaces": ["ens192", "eno1"],
        })
        assert m.ram_total_mb == 16384
        assert m.network_interfaces == ["ens192", "eno1"]

    def test_negative_ram_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "ram_total_mb": -1})

    def test_bad_interface_name_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "network_interfaces": ["eth 0;rm"]})

    def test_disk_usage_fields_accepted(self):
        m = InventoryCallbackRequest(**{**_BASE, "disks": [
            {"name": "sda", "size_gb": 500, "used_gb": 225,
             "used_percent": 48.4, "is_system": True},
        ]})
        d = m.disks[0]
        assert d.used_gb == 225
        assert d.used_percent == 48.4

    def test_disk_usage_optional(self):
        m = InventoryCallbackRequest(**{**_BASE, "disks": [
            {"name": "sda", "size_gb": 500, "is_system": False},
        ]})
        assert m.disks[0].used_gb is None
        assert m.disks[0].used_percent is None

    def test_used_percent_over_100_rejected(self):
        with pytest.raises(ValidationError):
            InventoryCallbackRequest(**{**_BASE, "disks": [
                {"name": "sda", "size_gb": 500, "used_percent": 101},
            ]})


class TestUsersInventoryCap:
    def _user(self, idx: int) -> dict:
        return {
            "login": f"user{idx}",
            "uid": 1000 + idx,
            "has_sudo": False,
            "unix_groups": [],
        }

    def test_1000_users_accepted(self):
        users = [self._user(i) for i in range(1000)]
        m = UsersInventoryCallbackRequest(users=users)
        assert len(m.users) == 1000

    def test_1001_users_rejected(self):
        users = [self._user(i) for i in range(1001)]
        with pytest.raises(ValidationError):
            UsersInventoryCallbackRequest(users=users)
