"""Тесты файлового бэкенда keystore (`FileKeyStore`).

Без k8s — инстанцируем `FileKeyStore` напрямую на tmp-пути. Проверяем
round-trip set/get, bootstrap из env при отсутствии файла, list/remove,
запрет retire активной версии и durability (перечитка с диска новым
инстансом).
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from src.core.exceptions import AppException
from src.core.keystore import FileKeyStore


def _path(tmp_path) -> str:
    return str(tmp_path / "ks.json")


class TestRoundTrip:
    def test_set_get_active_roundtrip(self, tmp_path):
        ks = FileKeyStore(_path(tmp_path))
        ks.set_key(5, "material-five")
        ks.set_active(5)
        assert ks.get_active_version() == 5
        assert ks.get_key(5) == b"material-five"

    def test_get_missing_version_raises(self, tmp_path):
        ks = FileKeyStore(_path(tmp_path))
        with pytest.raises(AppException) as exc:
            ks.get_key(999)
        assert exc.value.error_code == "ENCRYPTION_KEY_MISSING"

    def test_set_active_unknown_version_raises(self, tmp_path):
        ks = FileKeyStore(_path(tmp_path))
        with pytest.raises(AppException) as exc:
            ks.set_active(42)
        assert exc.value.error_code == "ENCRYPTION_KEY_MISSING"


class TestBootstrapFromEnv:
    def test_bootstrap_seeds_active_and_legacy(self, tmp_path, monkeypatch):
        # Активный мастер обязан пройти Settings-валидацию (min_length=32),
        # поэтому материал паддится до >=32 символов; legacy-версии берутся
        # из env-скана и под валидатор не попадают.
        active_material = "active-material-padded-to-32-chars!!"
        legacy_material = "legacy-material-padded-to-32-chars!"
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY", active_material)
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY_VERSION", "2")
        monkeypatch.setenv("SERVER_ENCRYPTION_KEY__v1", legacy_material)
        # config-settings кэшируются — обновим snapshot под новый env.
        from src.core import config as config_mod

        config_mod.get_settings.cache_clear()
        ks = FileKeyStore(_path(tmp_path))
        config_mod.get_settings.cache_clear()
        assert ks.get_active_version() == 2
        assert ks.get_key(2) == active_material.encode()
        assert ks.get_key(1) == legacy_material.encode()
        assert ks.list_versions() == [1, 2]

    def test_bootstrap_writes_file_on_first_run(self, tmp_path):
        path = _path(tmp_path)
        assert not os.path.exists(path)
        FileKeyStore(path)
        assert os.path.exists(path)
        raw = json.loads(open(path, encoding="utf-8").read())
        assert "active_version" in raw and "keys" in raw

    def test_file_is_0600(self, tmp_path):
        path = _path(tmp_path)
        FileKeyStore(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600


class TestListAndRemove:
    def test_list_versions_sorted(self, tmp_path):
        ks = FileKeyStore(_path(tmp_path))
        ks.set_key(3, "c")
        ks.set_key(1, "a")
        ks.set_key(2, "b")
        assert ks.list_versions() == sorted(set(ks.list_versions()))
        assert {1, 2, 3}.issubset(set(ks.list_versions()))

    def test_remove_non_active(self, tmp_path):
        ks = FileKeyStore(_path(tmp_path))
        ks.set_key(7, "seven")
        ks.set_active(7)
        ks.set_key(6, "six")
        ks.remove_key(6)
        assert 6 not in ks.list_versions()

    def test_cannot_retire_active(self, tmp_path):
        ks = FileKeyStore(_path(tmp_path))
        ks.set_key(8, "eight")
        ks.set_active(8)
        with pytest.raises(AppException) as exc:
            ks.remove_key(8)
        assert exc.value.error_code == "KEYSTORE_CANNOT_RETIRE_ACTIVE"

    def test_remove_unknown_is_noop(self, tmp_path):
        ks = FileKeyStore(_path(tmp_path))
        ks.set_key(9, "nine")
        ks.set_active(9)
        ks.remove_key(123)  # не падает
        assert 123 not in ks.list_versions()


class TestDurability:
    def test_state_survives_reopen(self, tmp_path):
        path = _path(tmp_path)
        ks = FileKeyStore(path)
        ks.set_key(4, "four-material")
        ks.set_active(4)
        reopened = FileKeyStore(path)
        assert reopened.get_active_version() == 4
        assert reopened.get_key(4) == b"four-material"
