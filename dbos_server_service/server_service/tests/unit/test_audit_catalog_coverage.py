"""Smoke-guard: каждое реально эмитимое audit-действие есть в каталоге.

`SERVICE_EVENTS` (`src/services/audit_events.py`) — список, который сервис
регистрирует в loging_service на startup. Если код начинает эмитить новый
action, забытый в каталоге, loging_service не узнает про его severity-default.

Тест статически вытаскивает имена action'ов из call-site'ов `emit(...)`,
`emit_denied_on_authz_error(...)` и присваиваний `audit_action = "..."`,
и проверяет, что каждый есть в `SERVICE_EVENTS`. Каталог — superset (часть
событий эмитится через mapping-таблицы / middleware и не ловится grep'ом,
это нормально), поэтому проверяется только направление emit ⊆ каталог.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.services.audit_events import SERVICE_EVENTS

_SRC = Path(__file__).resolve().parents[2] / "src"
_AUDIT_EVENTS_FILE = _SRC / "services" / "audit_events.py"

_EMIT_PATTERNS = (
    re.compile(r'\bemit\(\s*\n?\s*"([a-z_]+\.[a-z_]+)"'),
    re.compile(r'emit_denied_on_authz_error\(\s*\n\s*"([a-z_]+\.[a-z_]+)"'),
    re.compile(r'audit_action\s*=\s*"([a-z_]+\.[a-z_]+)"'),
)


def _collect_emitted_actions() -> set[str]:
    actions: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if path == _AUDIT_EVENTS_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _EMIT_PATTERNS:
            actions.update(pattern.findall(text))
    return actions


def test_every_emitted_action_is_in_catalog():
    catalog = {event["action"] for event in SERVICE_EVENTS}
    emitted = _collect_emitted_actions()
    missing = sorted(emitted - catalog)
    assert missing == [], (
        f"actions emitted in src/ but absent from SERVICE_EVENTS: {missing}"
    )


def test_collector_finds_emit_sites():
    # Защита от тихого протухания регэкспов: если ни один emit не нашёлся —
    # значит паттерны рассинхронизировались с кодом, а не код стал чистым.
    assert len(_collect_emitted_actions()) > 20
