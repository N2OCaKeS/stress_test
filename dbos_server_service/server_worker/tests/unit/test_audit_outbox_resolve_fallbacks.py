"""Unit-тесты fallback-веток `_resolve_batch_size` / `_resolve_poll_interval`
/ `_resolve_cb_sleep_chunk`.

Каждый из трёх resolver'ов читает свежий `Settings()` (без LRU-кэша,
тесты могут менять env через `monkeypatch.setenv`). Если pydantic-
валидация падает (malformed env, отсутствие .env-файла и т.п.), resolver
возвращает module-level default'ы, чтобы publisher не остановился из-за
конфиг-ошибки.

Эта ветка проверяется через прямой monkeypatch на `Settings` в модуле
publisher'а — конкретный механизм поломки valid'ации тестам не важен, важен
сам fallback.
"""

from __future__ import annotations

import pytest

from src.services import audit_outbox_publisher


def _make_boom_settings() -> type:
    class _BoomSettings:
        def __init__(self) -> None:
            raise ValueError("simulated malformed env")

    return _BoomSettings


class TestResolveFallbacks:
    def test_batch_size_falls_back_on_settings_error(self, monkeypatch):
        monkeypatch.setattr(
            audit_outbox_publisher, "Settings", _make_boom_settings()
        )
        result = audit_outbox_publisher._resolve_batch_size()
        assert result == audit_outbox_publisher._BATCH_SIZE

    def test_poll_interval_falls_back_on_settings_error(self, monkeypatch):
        monkeypatch.setattr(
            audit_outbox_publisher, "Settings", _make_boom_settings()
        )
        result = audit_outbox_publisher._resolve_poll_interval()
        assert result == audit_outbox_publisher._POLL_INTERVAL_SECONDS

    def test_cb_sleep_chunk_falls_back_on_settings_error(self, monkeypatch):
        monkeypatch.setattr(
            audit_outbox_publisher, "Settings", _make_boom_settings()
        )
        result = audit_outbox_publisher._resolve_cb_sleep_chunk()
        assert result == audit_outbox_publisher._CB_SLEEP_CHUNK_SECONDS

    @pytest.mark.parametrize(
        "resolver_name, default_name",
        [
            ("_resolve_batch_size", "_BATCH_SIZE"),
            ("_resolve_poll_interval", "_POLL_INTERVAL_SECONDS"),
            ("_resolve_cb_sleep_chunk", "_CB_SLEEP_CHUNK_SECONDS"),
        ],
    )
    def test_happy_path_returns_settings_value(
        self, monkeypatch, resolver_name, default_name
    ):
        """На корректном Settings resolver возвращает live-значение из него,
        а не module-default."""
        # Каждый resolver читает свой attr — кладём в fake Settings все три,
        # чтобы параметризованный тест работал на любом из них.
        class _FakeSettings:
            audit_outbox_batch_size = 999
            audit_outbox_poll_interval_seconds = 7.5
            audit_outbox_cb_sleep_chunk_seconds = 3.25

        monkeypatch.setattr(audit_outbox_publisher, "Settings", _FakeSettings)
        result = getattr(audit_outbox_publisher, resolver_name)()
        default = getattr(audit_outbox_publisher, default_name)
        assert result != default
