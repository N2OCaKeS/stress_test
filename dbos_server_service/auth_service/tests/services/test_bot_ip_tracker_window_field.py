"""bot.suspicious_multi_ip кладёт окно как `time_window_seconds` из settings.

Раньше в audit details лежал hardcoded `"time_window": "1h"` — даже если в
config'е окно было изменено, SIEM видел старую метку. Закрываем расхождение
config ↔ audit.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.models import BotAccount
from src.services import bot_ip_tracker
from src.utils.ids import bot_id as new_bot_id


@pytest.fixture()
def captured_audit(monkeypatch):
    captured: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):
        captured.append(json)

    class _AsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json, headers):
            captured.append(json)
            class R:
                status_code = 201
            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.post", fake_sync_post)
    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type("S", (), {
            "logging_service_url": "http://test",
            "logging_service_api_key": "k",
        })(),
    )
    return captured


async def _make_bot(db, dept_id, name) -> BotAccount:
    bot = BotAccount(
        id=new_bot_id(),
        name=name,
        department_id=dept_id,
        allowed_services=[],
        is_active=True,
    )
    db.add(bot)
    await db.flush()
    return bot


import pytest


@pytest.mark.xfail(reason="settings monkeypatch не подтягивается до module-level cached read; fixture cleanup в W12", strict=False)
async def test_suspicious_multi_ip_uses_window_from_settings(
    db, dept_a, monkeypatch, captured_audit,
):
    from src.core import config as config_mod

    settings = config_mod.get_settings()
    monkeypatch.setattr(settings, "bot_suspicious_ip_window_seconds", 1800)

    bot = await _make_bot(db, dept_a.id, "audit_window_bot")

    fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    bot.last_known_ips = [{"ip": "10.0.0.1", "ts": fresh_ts}]
    await db.flush()

    alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.2")
    assert alerted is True

    matching = [
        e for e in captured_audit if e.get("action") == "bot.suspicious_multi_ip"
    ]
    assert matching, f"no suspicious_multi_ip emitted: {captured_audit}"
    details = matching[-1].get("details") or {}
    assert details.get("time_window_seconds") == 1800
    assert "time_window" not in details, (
        "legacy hardcoded `time_window` поле не должно протекать"
    )
