"""Выбор снимка ACS по `{hostname}-{version}` с нормализацией версии."""

from __future__ import annotations

import pytest

from src.core.exceptions import NotFoundError
from src.services import acs_snapshot_lookup as lookup


class TestHostSnapshots:
    def test_prefix_includes_dash(self) -> None:
        """`LowServer2-…` не относится к `LowServer`."""
        items = lookup.host_snapshots(
            ["LowServer2-1710rc52", "LowServer-1710rc52", "LowServer", "LowServer-"],
            "LowServer",
        )
        assert [item.name for item in items] == ["LowServer-1710rc52"]

    def test_tail_kept_as_is_and_normalized_separately(self) -> None:
        (item,) = lookup.host_snapshots(["LowServer-1710rc52"], "LowServer")
        assert item.version_name == "1710rc52"
        assert item.normalized_version == "1.7.10.52"

    def test_sorted_by_name(self) -> None:
        items = lookup.host_snapshots(
            ["LowServer-1.8.1.6", "LowServer-1710rc52", "LowServer-1.7.10.52"],
            "LowServer",
        )
        assert [item.name for item in items] == sorted(item.name for item in items)


class TestPickSnapshot:
    @pytest.mark.parametrize(
        ("names", "version", "expected_tail"),
        [
            (["LowServer-1710rc52"], "1.7.10.52", "1710rc52"),
            (["LowServer-1.7.10.52"], "1.7.10.52", "1.7.10.52"),
            # Каталог хранит компактное имя (заведено вручную) — тоже находится.
            (["LowServer-1.7.10.52"], "1710rc52", "1.7.10.52"),
            # Несколько кандидатов — точное совпадение с именем версии.
            (["LowServer-1710rc52", "LowServer-1.7.10.52"], "1.7.10.52", "1.7.10.52"),
            (["LowServer-1710rc52", "LowServer-1.7.10.52"], "1710rc52", "1710rc52"),
        ],
    )
    def test_match(self, names, version, expected_tail) -> None:
        chosen = lookup.pick_snapshot(lookup.host_snapshots(names, "LowServer"), version)
        assert chosen is not None
        assert chosen.version_name == expected_tail

    def test_several_candidates_without_exact_match_first_by_name(self) -> None:
        # Оба хвоста нормализуются в `1.7.10.52`, но ни один не равен
        # `" 1.7.10.52 "` дословно — берём первый по сортировке имён.
        items = lookup.host_snapshots(["LowServer-1710rc52", "LowServer-1.7.10.52"], "LowServer")
        chosen = lookup.pick_snapshot(items, " 1.7.10.52 ")
        assert chosen is not None
        assert chosen.name == "LowServer-1.7.10.52"

    def test_other_version_not_matched(self) -> None:
        items = lookup.host_snapshots(["LowServer-1710rc64"], "LowServer")
        assert lookup.pick_snapshot(items, "1.7.10.52") is None


class TestFindSnapshotForRestore:
    async def test_not_found_names_expected_snapshot(self, monkeypatch) -> None:
        async def fake_names(db):
            return ["LowServer2-1710rc52", "LowServer-1710rc64"]

        monkeypatch.setattr(lookup, "list_acs_snapshot_names", fake_names)
        with pytest.raises(NotFoundError) as excinfo:
            await lookup.find_snapshot_for_restore(
                None, hostname="LowServer", version_name="1.7.10.52",
            )
        assert excinfo.value.error_code == "ACS_SNAPSHOT_NOT_FOUND"
        assert "LowServer-1.7.10.52" in excinfo.value.message
        assert excinfo.value.details["expected_name"] == "LowServer-1.7.10.52"
        assert excinfo.value.details["host_snapshots"] == ["LowServer-1710rc64"]

    async def test_found_uses_acs_list(self, monkeypatch) -> None:
        from src.services import acs_client, acs_settings

        async def fake_creds(db):
            return "http://acs.test", "pwd"

        async def fake_list(base_url, password):
            assert (base_url, password) == ("http://acs.test", "pwd")
            return ["LowServer-1710rc52"]

        monkeypatch.setattr(acs_settings, "get_acs_credentials", fake_creds)
        monkeypatch.setattr(acs_client, "list_snapshots", fake_list)
        chosen = await lookup.find_snapshot_for_restore(
            None, hostname="LowServer", version_name="1.7.10.52",
        )
        assert chosen.name == "LowServer-1710rc52"
        assert chosen.version_name == "1710rc52"
