"""Каталог worker-audit-событий и его регистрация в loging_service.

Покрывает `services/audit_events.py`:
  * `SERVICE_EVENTS` непустой, без дублей, с валидными severity и формой
    записи `{action, description, default_severity}`;
  * каждый action, который worker реально эмитит в коде, присутствует в
    каталоге (drift между кодом и регистрацией);
  * `register_events` шлёт POST на правильный URL с
    `X-Service-Identity: server_worker` и полным списком; skip'ает без
    конфига; retry'ит transient 5xx.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from src.services import audit_events


_VALID_SEVERITIES = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_WORKER_SRC = Path(__file__).resolve().parents[1] / "src"


class TestCatalogShape:
    def test_non_empty(self):
        assert audit_events.SERVICE_EVENTS

    def test_record_shape_and_severity(self):
        for ev in audit_events.SERVICE_EVENTS:
            assert set(ev) == {"action", "description", "default_severity"}, ev
            assert ev["action"] and isinstance(ev["action"], str)
            assert ev["description"] and isinstance(ev["description"], str)
            assert ev["default_severity"] in _VALID_SEVERITIES, ev

    def test_no_duplicate_actions(self):
        actions = [ev["action"] for ev in audit_events.SERVICE_EVENTS]
        assert len(actions) == len(set(actions))


class TestCatalogCoversEmittedActions:
    """Все action'ы, эмитируемые worker'ом в коде, есть в каталоге."""

    def _emitted_actions(self) -> set[str]:
        actions: set[str] = set()
        # Статические литералы "action": "..." и audit_action="..." по src/.
        literal = re.compile(r'"action":\s*"([a-z0-9_]+(?:\.[a-z0-9_]+)+)"')
        kwarg = re.compile(r'audit_action="([a-z0-9_]+(?:\.[a-z0-9_]+)+)"')
        for py in _WORKER_SRC.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            actions.update(literal.findall(text))
            actions.update(kwarg.findall(text))
        # Динамические (audit_action передаётся аргументом, не литералом) и
        # чужие каталоги в код-строке отсеиваем ниже.
        return actions

    def test_all_emitted_actions_registered(self):
        registered = {ev["action"] for ev in audit_events.SERVICE_EVENTS}
        emitted = self._emitted_actions()
        # `worker_dispatch.orphan_detected` эмитит server_service (на своей
        # стороне dispatch_outbox), не worker — в его каталоге не нужен.
        emitted.discard("worker_dispatch.orphan_detected")
        missing = emitted - registered
        assert not missing, (
            f"эти action'ы эмитятся в worker-коде, но не в SERVICE_EVENTS: {sorted(missing)}"
        )


class TestRegisterEvents:
    def test_skips_without_config(self, monkeypatch):
        posted: list = []
        monkeypatch.setattr(
            audit_events, "get_settings",
            lambda: SimpleNamespace(logging_service_url="", logging_service_api_key=""),
        )
        monkeypatch.setattr(audit_events.httpx, "post", lambda *a, **k: posted.append((a, k)))
        audit_events.register_events()
        assert posted == []

    def test_posts_full_catalog_with_identity(self, monkeypatch):
        captured: dict = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"total": len(audit_events.SERVICE_EVENTS), "added": 3, "updated": 0}

        def _fake_post(url, json, headers, timeout):  # noqa: A002 — mirror httpx.post kwarg
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

        monkeypatch.setattr(
            audit_events, "get_settings",
            lambda: SimpleNamespace(
                logging_service_url="http://loging:8001/",
                logging_service_api_key="svc-key",
            ),
        )
        monkeypatch.setattr(audit_events.httpx, "post", _fake_post)

        audit_events.register_events()

        assert captured["url"] == "http://loging:8001/api/logging/v1/services/server_worker/events"
        assert captured["headers"]["X-Service-Identity"] == "server_worker"
        assert captured["headers"]["Authorization"] == "Bearer svc-key"
        assert captured["json"]["events"] == audit_events.SERVICE_EVENTS

    def test_retries_transient_5xx(self, monkeypatch):
        calls: list[int] = []

        class _Resp5xx:
            status_code = 503
            text = "unavailable"

        class _Resp200:
            status_code = 200

            @staticmethod
            def json():
                return {"total": 1, "added": 0, "updated": 1}

        def _fake_post(*a, **k):
            calls.append(1)
            return _Resp5xx() if len(calls) == 1 else _Resp200()

        monkeypatch.setattr(
            audit_events, "get_settings",
            lambda: SimpleNamespace(
                logging_service_url="http://loging:8001",
                logging_service_api_key="svc-key",
            ),
        )
        monkeypatch.setattr(audit_events.httpx, "post", _fake_post)
        monkeypatch.setattr(audit_events.time, "sleep", lambda *_: None)

        audit_events.register_events()
        assert len(calls) == 2
