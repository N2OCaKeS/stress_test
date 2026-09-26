"""Smoke tests — module imports, broker registers the placeholder task.

Structural checks only, no live Redis: importing `src.main` must build the
broker and register `system.ping` without opening a connection. Mirrors how
`testing_service/tests/test_health.py` checks the API skeleton is wired
together, just for the worker side.
"""

from __future__ import annotations


def test_broker_imports():
    from src.core.broker import broker

    assert broker is not None


def test_main_registers_system_ping():
    """Importing `src.main` must register `system.ping` on the broker."""
    from src.main import broker

    registered = set(broker.get_all_tasks().keys())
    assert "system.ping" in registered


def test_tasks_package_imports():
    """Empty for now but must import cleanly."""
    from src import tasks

    assert tasks is not None


def test_settings_defaults():
    from src.core.config import get_settings

    settings = get_settings()
    assert settings.app_env
    assert settings.redis_url
    assert settings.taskiq_queue_name
