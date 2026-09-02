"""LRU-кэп `_REVEAL_AUDIT_WINDOW` в server_account.py / ipmi_controller.py.

Throttle-словарь жил без ограничения по размеру: stale-sweep срабатывает
только на cache-miss того же окна, и при штурме длинной серии уникальных
(actor, account) / (actor, controller) пар словарь распухал между
sweep'ами. Теперь — OrderedDict с кэпом `_REVEAL_AUDIT_WINDOW_MAX`
и LRU-eviction'ом на overflow.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_window_settings(monkeypatch):
    """Большое окно + чистый словарь, иначе тесты ловят stale-sweep друг друга."""
    from src.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "password_reveal_audit_window_seconds", 3600, raising=False,
    )
    yield


def test_server_account_window_caps_at_max(monkeypatch):
    from src.services import server_account as sa_mod

    monkeypatch.setattr(sa_mod, "_REVEAL_AUDIT_WINDOW_MAX", 10000)
    sa_mod._REVEAL_AUDIT_WINDOW.clear()

    # 10001 уникальных actor'ов → перепрыгиваем кэп. После каждого insert'а
    # OrderedDict должен подрезать самый старый ключ.
    for i in range(10001):
        sa_mod._record_reveal_attempt(actor_id=f"actor_{i}", account_id="acc_x")
    assert len(sa_mod._REVEAL_AUDIT_WINDOW) <= 10000
    # Первый actor вытеснен (LRU), последний — на месте.
    assert ("actor_0", "acc_x") not in sa_mod._REVEAL_AUDIT_WINDOW
    assert ("actor_10000", "acc_x") in sa_mod._REVEAL_AUDIT_WINDOW


def test_server_account_window_lru_eviction_keeps_recent(monkeypatch):
    """LRU: `move_to_end` обновляет порядок при hit'е, старый ключ не вытесняется
    если к нему недавно обращались."""
    from src.services import server_account as sa_mod

    monkeypatch.setattr(sa_mod, "_REVEAL_AUDIT_WINDOW_MAX", 5)
    sa_mod._REVEAL_AUDIT_WINDOW.clear()

    for i in range(5):
        sa_mod._record_reveal_attempt(actor_id=f"a_{i}", account_id="acc")
    # Touch a_0 — он перепрыгивает в конец очереди (most-recent).
    sa_mod._record_reveal_attempt(actor_id="a_0", account_id="acc")
    # Доливаем новый — вытесниться должен a_1, не a_0.
    sa_mod._record_reveal_attempt(actor_id="a_new", account_id="acc")

    assert len(sa_mod._REVEAL_AUDIT_WINDOW) == 5
    assert ("a_0", "acc") in sa_mod._REVEAL_AUDIT_WINDOW
    assert ("a_1", "acc") not in sa_mod._REVEAL_AUDIT_WINDOW
    assert ("a_new", "acc") in sa_mod._REVEAL_AUDIT_WINDOW


def test_ipmi_controller_window_caps_at_max(monkeypatch):
    from src.services import ipmi_controller as ipmi_mod

    monkeypatch.setattr(ipmi_mod, "_REVEAL_AUDIT_WINDOW_MAX", 10000)
    ipmi_mod._REVEAL_AUDIT_WINDOW.clear()

    for i in range(10001):
        ipmi_mod._record_controller_reveal_attempt(
            actor_id=f"actor_{i}", controller_id="ctrl_x",
        )
    assert len(ipmi_mod._REVEAL_AUDIT_WINDOW) <= 10000
    assert ("actor_0", "ctrl_x") not in ipmi_mod._REVEAL_AUDIT_WINDOW
    assert ("actor_10000", "ctrl_x") in ipmi_mod._REVEAL_AUDIT_WINDOW
