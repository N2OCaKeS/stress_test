"""Тесты для `services.bot_ip_tracker.track_bot_ip`.

Сценарии:
  * single IP — ничего не алертит, запись добавлена.
  * два разных IP в одном окне 1h — алерт CRITICAL.
  * запись с timestamp вне окна — не считается; одиночный свежий IP без алерта.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import BotAccount
from src.services import audit_service as audit_mod
from src.services import bot_ip_tracker
from src.utils.ids import bot_id


async def _make_bot(db, dept_id, name="ip_tracker_bot") -> BotAccount:
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


def _capture_emits(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


class TestTrackBotIp:
    async def test_single_ip_no_alert(self, db, dept_a, monkeypatch):
        """Один IP — запись добавлена, `bot.suspicious_multi_ip` не эмитится."""
        bot = await _make_bot(db, dept_a.id, "single_ip_bot")
        captured = _capture_emits(monkeypatch)

        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")
        assert alerted is False

        assert len(bot.last_known_ips) == 1
        assert bot.last_known_ips[0]["ip"] == "10.0.0.1"

        suspicious = [e for e in captured if e["action"] == "bot.suspicious_multi_ip"]
        assert suspicious == []

    async def test_two_distinct_ips_in_window_alert(self, db, dept_a, monkeypatch):
        """Два разных IP за час → CRITICAL audit + оба IP в details.ips."""
        bot = await _make_bot(db, dept_a.id, "two_ip_bot")
        captured = _capture_emits(monkeypatch)

        first = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")
        assert first is False
        second = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.2")
        assert second is True

        assert len(bot.last_known_ips) == 2
        assert [e["ip"] for e in bot.last_known_ips] == ["10.0.0.1", "10.0.0.2"]

        alerts = [e for e in captured if e["action"] == "bot.suspicious_multi_ip"]
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert["actor_id"] == bot.id
        assert alert.get("actor_type") == "bot"
        assert alert.get("status") == "failure"
        assert alert.get("allowed") is False
        details = alert["details"]
        assert details["bot_id"] == bot.id
        assert details["bot_name"] == bot.name
        # W11-W2: hardcoded 'time_window: 1h' заменено на time_window_seconds
        # из Settings (default 3600 = 1h).
        assert details["time_window_seconds"] == 3600
        assert set(details["ips"]) == {"10.0.0.1", "10.0.0.2"}

    async def test_old_record_outside_window_ignored(self, db, dept_a, monkeypatch):
        """Запись с ts старше 1h не считается уникальным IP, алерта нет."""
        bot = await _make_bot(db, dept_a.id, "old_record_bot")
        # Притворяемся, что бот вчера светился с 10.0.0.1; сегодня — 10.0.0.2.
        # Это >1h, поэтому свежим уникальным останется только 10.0.0.2 → no alert.
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        bot.last_known_ips = [{"ip": "10.0.0.1", "ts": stale_ts}]
        await db.flush()

        captured = _capture_emits(monkeypatch)
        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.2")
        assert alerted is False

        ips = [e["ip"] for e in bot.last_known_ips]
        assert "10.0.0.2" in ips

        suspicious = [e for e in captured if e["action"] == "bot.suspicious_multi_ip"]
        assert suspicious == []

    async def test_third_ip_triggers_alert(self, db, dept_a, monkeypatch):
        """Третий разный IP в окне 1h → алерт со всеми тремя IP."""
        bot = await _make_bot(db, dept_a.id, "third_ip_bot")
        await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")
        await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.2")

        # Capture только третий вызов, чтобы убедиться, что и тут эмитится.
        captured = _capture_emits(monkeypatch)
        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.3")
        assert alerted is True

        alerts = [e for e in captured if e["action"] == "bot.suspicious_multi_ip"]
        assert len(alerts) == 1
        assert set(alerts[0]["details"]["ips"]) == {"10.0.0.1", "10.0.0.2", "10.0.0.3"}

    async def test_repeat_same_ip_updates_ts_no_duplicate(self, db, dept_a, monkeypatch):
        """Повтор того же IP не плодит дубли в window, ts последнего обновляется."""
        bot = await _make_bot(db, dept_a.id, "repeat_ip_bot")
        captured = _capture_emits(monkeypatch)

        await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")
        first_ts = bot.last_known_ips[0]["ts"]
        await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")

        assert len(bot.last_known_ips) == 1
        assert bot.last_known_ips[0]["ip"] == "10.0.0.1"
        # ts может совпасть с точностью до микросекунды, поэтому проверяем
        # только что элемент один (не задублирован) и алерт не сработал.
        assert bot.last_known_ips[0]["ts"] >= first_ts

        alerts = [e for e in captured if e["action"] == "bot.suspicious_multi_ip"]
        assert alerts == []

    async def test_caller_ip_none_skipped(self, db, dept_a, monkeypatch):
        """`caller_ip=None` — детектор тихо пропускает, ничего не пишет."""
        bot = await _make_bot(db, dept_a.id, "none_ip_bot")
        captured = _capture_emits(monkeypatch)

        alerted = await bot_ip_tracker.track_bot_ip(db, bot, None)
        assert alerted is False
        assert list(bot.last_known_ips) == []
        assert [e for e in captured if e["action"] == "bot.suspicious_multi_ip"] == []

    async def test_repeat_same_ip_does_not_mutate_original_entry(self, db, dept_a):
        """Refresh ts для повторного IP не должен мутировать словарь, на который
        могла остаться ссылка вне tracker'а (защита от shallow-copy ловушки)."""
        bot = await _make_bot(db, dept_a.id, "shared_ref_bot")
        await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")

        # Снимок исходной записи: до фикса tracker писал в `window[-1]["ts"]`
        # in-place, и snapshot тоже бы менялся.
        original_entry = bot.last_known_ips[0]
        snapshot_ts = original_entry["ts"]

        await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")

        # Внешний snapshot остался нетронутым; в bot теперь новый dict.
        assert original_entry["ts"] == snapshot_ts
        assert bot.last_known_ips[0] is not original_entry

    async def test_fifo_trim_at_default_window_size(self, db, dept_a):
        """С дефолтным окном 5 шестой IP вытесняет первый, размер списка не растёт."""
        bot = await _make_bot(db, dept_a.id, "fifo_default_bot")

        ips = [f"10.0.0.{i}" for i in range(1, 7)]  # .1..6
        for ip in ips:
            await bot_ip_tracker.track_bot_ip(db, bot, ip)

        assert len(bot.last_known_ips) == 5
        # .1 (самый старый) вытеснен; .2..6 остались в FIFO-порядке.
        assert [e["ip"] for e in bot.last_known_ips] == ips[1:]

    def test_parse_ts_handles_non_string_inputs(self):
        """_parse_ts ловит TypeError, если в JSONB лежит не строка (int, dict, list).

        fromisoformat для не-строки кидает TypeError, не ValueError — без явного
        `except (ValueError, TypeError)` битый row уронил бы весь tracker.
        """
        assert bot_ip_tracker._parse_ts(None) is None
        assert bot_ip_tracker._parse_ts("") is None
        assert bot_ip_tracker._parse_ts("not-an-iso") is None
        # Не-строковые значения — самое интересное: PostgreSQL JSONB и кривой
        # production-row теоретически могут вернуть что угодно.
        assert bot_ip_tracker._parse_ts(123) is None  # type: ignore[arg-type]
        assert bot_ip_tracker._parse_ts({"x": 1}) is None  # type: ignore[arg-type]
        assert bot_ip_tracker._parse_ts(["2024-01-01"]) is None  # type: ignore[arg-type]

    async def test_invalid_ts_record_does_not_break_tracker(self, db, dept_a, monkeypatch):
        """Запись с невалидным `ts` (не ISO) — игнорируется при подсчёте уникальных IP,
        не падает, но FIFO-trim её всё ещё двигает как обычный элемент окна."""
        bot = await _make_bot(db, dept_a.id, "bad_ts_bot")
        # Подсовываем мусорную запись напрямую в JSONB — такая может остаться
        # от старого формата или ручной правки в БД.
        bot.last_known_ips = [{"ip": "10.0.0.99", "ts": "not-an-iso-string"}]
        await db.flush()

        captured = _capture_emits(monkeypatch)
        alerted = await bot_ip_tracker.track_bot_ip(db, bot, "10.0.0.1")

        # Невалидный ts → запись не учитывается как уникальный IP, алерт не сработал.
        assert alerted is False
        # Сам tracker не упал, новая запись добавлена в окно.
        assert any(e["ip"] == "10.0.0.1" for e in bot.last_known_ips)
        assert [e for e in captured if e["action"] == "bot.suspicious_multi_ip"] == []

    async def test_window_size_respects_settings(self, db, dept_a, monkeypatch):
        """`bot_last_known_ips_window` из Settings рулит размером окна."""
        from src.core import config as config_mod

        # Уменьшаем окно до 2, чтобы не зависеть от дефолта.
        original = config_mod.get_settings()
        monkeypatch.setattr(
            original, "bot_last_known_ips_window", 2,
        )
        # get_settings()@lru_cache отдаёт тот же объект, мутации хватит.

        bot = await _make_bot(db, dept_a.id, "window_size_bot")
        for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
            await bot_ip_tracker.track_bot_ip(db, bot, ip)

        assert len(bot.last_known_ips) == 2
        # FIFO: самый старый (.1) вытеснен.
        assert [e["ip"] for e in bot.last_known_ips] == ["10.0.0.2", "10.0.0.3"]
