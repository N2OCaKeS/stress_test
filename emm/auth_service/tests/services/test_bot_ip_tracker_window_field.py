"""bot.suspicious_multi_ip кладёт окно как `time_window_seconds` из settings.

Раньше в audit details лежал hardcoded `"time_window": "1h"` — даже если в
config'е окно было изменено, SIEM видел старую метку. Закрываем расхождение
config ↔ audit.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.models import BotAccount
from src.services import audit_service as audit_mod
from src.services import bot_ip_tracker
from src.utils.ids import bot_id as new_bot_id


@pytest.fixture()
def captured_audit(monkeypatch):
    """Spy на `audit_service.emit` — без HTTP-обёрток.

    Раньше тест патчил `httpx.post` + `httpx.AsyncClient` + `get_settings`
    и ловил payload на сетевом уровне. Под emit'ом сидит
    `loop.create_task(_send_to_logging_service(...))` — задача планируется
    в фоне и до конца теста может не успеть `await` HTTP-call'у, capture
    оставался пустым. Прямой spy на `emit` снимает payload синхронно.
    """
    captured: list[dict] = []
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
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


async def test_suspicious_multi_ip_uses_window_from_settings(
    db, dept_a, monkeypatch, captured_audit,
):
    from src.core import config as config_mod

    # `get_settings` — lru_cache-singleton, поэтому monkeypatch на сам объект
    # подменяет значение для всех последующих читателей в этом тесте.
    # `cache_clear()` тут не нужен: tracker берёт settings в runtime, и
    # после fixture-teardown'а monkeypatch вернёт оригинал.
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
