"""bot_ip_tracker — покрытие веток, не охваченных test_bot_ip_tracker.py.

W8: проверяем ветку `bot_suspicious_ip_window_seconds` из Settings и
поведение трекера при граничном размере окна (window_size=1).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import BotAccount
from src.services import bot_ip_tracker
from src.utils.ids import bot_id


async def _make_bot(db, dept_id, name) -> BotAccount:
    bot = BotAccount(
        id=bot_id(),
        name=name,
        department_id=dept_id,
        allowed_services=[],
        is_active=True,
    )
    db.add(bot)
    await db.flush()
    return bot


class TestBotIpTrackerWindowSeconds:
    async def test_suspicious_window_seconds_custom_value(
        self, db, dept_a, monkeypatch,
    ):
        """Маленькое `bot_suspicious_ip_window_seconds` (1s) → запись старше 1s
        не считается в окне, алерт не срабатывает.

        Тест проверяет, что трекер читает `suspicious_window` именно из settings,
        а не хардкодит.
        """
        from src.core import config as config_mod

        settings = config_mod.get_settings()
        monkeypatch.setattr(settings, "bot_suspicious_ip_window_seconds", 1)

        bot = await _make_bot(db, dept_a.id, "short_window_bot")

        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        bot.last_known_ips = [{"ip": "10.0.0.1", "ts": stale_ts}]
        await db.flush()

        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.2")
        assert alerted is False, (
            "стаый IP старше 1s не должен считаться в коротком окне"
        )

    async def test_two_ips_within_custom_window_triggers_alert(
        self, db, dept_a, monkeypatch,
    ):
        """Два IP в пределах кастомного окна → алерт."""
        from src.core import config as config_mod

        settings = config_mod.get_settings()
        monkeypatch.setattr(settings, "bot_suspicious_ip_window_seconds", 3600)

        bot = await _make_bot(db, dept_a.id, "custom_window_alert_bot")

        fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        bot.last_known_ips = [{"ip": "10.0.0.1", "ts": fresh_ts}]
        await db.flush()

        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.2")
        assert alerted is True, "оба IP в пределах 1h → алерт ожидается"

    async def test_window_size_one_evicts_old_entry(
        self, db, dept_a, monkeypatch,
    ):
        """При window_size=1 каждый новый IP вытесняет предыдущий;
        окно никогда не превышает 1 запись."""
        from src.core import config as config_mod

        settings = config_mod.get_settings()
        monkeypatch.setattr(settings, "bot_last_known_ips_window", 1)

        bot = await _make_bot(db, dept_a.id, "single_slot_bot")

        await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")
        assert len(bot.last_known_ips) == 1
        assert bot.last_known_ips[0]["ip"] == "10.0.0.1"

        # Следующий IP вытесняет первый.
        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.2")
        # Только один IP в окне → unique_ips < 2 → нет алерта.
        assert alerted is False
        assert len(bot.last_known_ips) == 1
        assert bot.last_known_ips[0]["ip"] == "10.0.0.2"

    async def test_empty_window_no_alert(self, db, dept_a):
        """Bot без истории IP → первый трекинг, алерт не должен сработать."""
        bot = await _make_bot(db, dept_a.id, "fresh_bot")
        assert bot.last_known_ips is None or bot.last_known_ips == []

        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "172.16.0.1")
        assert alerted is False
        assert len(bot.last_known_ips) == 1
        assert bot.last_known_ips[0]["ip"] == "172.16.0.1"
