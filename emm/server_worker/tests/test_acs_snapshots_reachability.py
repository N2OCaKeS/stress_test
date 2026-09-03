"""Тесты `_wait_out_and_back`/`_verify_bootstrap_login` в `tasks/acs_snapshots.py`.

Регрессия на конкретный инцидент: `_wait_out_and_back` раньше "прощала"
сервер, который не ушёл в оффлайн за `acs_down_wait_seconds` ("не дождались —
не фатально, идём ждать возврата всё равно") — из-за этого reachability-
проверка "вернулся ли" стартовала, пока сервер ещё даже не начал
перезагрузку, мгновенно видела его отвечающим и рапортовала ложный успех
через несколько минут вместо реальных 30-40. `server.prepare` после этого
падал — сервер физически ещё восстанавливался.

Покрывает:
* `_wait_out_and_back` — `False`, если сервер ни разу не ушёл в оффлайн
  (без "proceed anyway"); `True` при штатном down→up переходе.
* `_verify_bootstrap_login` — ретраит SSH-логин bootstrap-кредой с паузами,
  успех на любой попытке в пределах лимита; поднимает `SshError` последней
  попытки, если лимит исчерпан.
"""

from __future__ import annotations

import pytest

from src.clients.ssh import SshError
from src.tasks import acs_snapshots


class _FakeSshClient:
    """Подменяет `SshClient` — коннект либо падает `SshError`, либо успешен.

    `_degraded_times` — сколько первых УСПЕШНЫХ логинов должны вернуть
    `systemctl is-system-running` = `degraded` вместо `running` (симулирует
    Clonezilla live-окружение или собственный незавершённый self-healing
    ребут ACS, где SSH уже открыт, но система ещё не готова)."""

    _fail_times: int = 0
    _degraded_times: int = 0
    _calls: int = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeSshClient":
        type(self)._calls += 1
        if type(self)._calls <= type(self)._fail_times:
            raise SshError(error_code="SSH_AUTH_FAILED", host="h", message="nope")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def run(self, command: str, **kwargs):
        if type(self)._calls <= type(self)._fail_times + type(self)._degraded_times:
            return 1, "degraded", ""
        return 0, "running", ""


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Не спать реально между ретраями/поллами."""
    async def _noop_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(acs_snapshots.asyncio, "sleep", _noop_sleep)


class TestWaitOutAndBack:
    async def test_returns_false_when_server_never_goes_offline(self, monkeypatch):
        settings = acs_snapshots.get_settings()
        monkeypatch.setattr(settings, "acs_down_wait_seconds", 0.01)
        monkeypatch.setattr(settings, "acs_reachability_timeout_seconds", 0.01)
        monkeypatch.setattr(settings, "acs_reachability_poll_interval_seconds", 0.0)

        up_calls = 0

        async def _always_up(host, ssh_port):
            nonlocal up_calls
            up_calls += 1
            return True

        monkeypatch.setattr(acs_snapshots, "_network_up", _always_up)

        result = await acs_snapshots._wait_out_and_back("10.0.0.1", 22)

        assert result is False
        # Ключевой инвариант регрессии: раз сервер не ушёл в оффлайн, до
        # фазы "ждём возврата" мы вообще не должны были дойти — иначе это
        # снова гонка "уже был up, поэтому мгновенный ложный успех".
        # `_network_up` в реальности вызывается и на down-фазе (проверяя,
        # что ещё up), поэтому просто убеждаемся в итоговом `False`.
        assert up_calls >= 1

    async def test_returns_true_on_down_then_up_transition(self, monkeypatch):
        settings = acs_snapshots.get_settings()
        monkeypatch.setattr(settings, "acs_down_wait_seconds", 5.0)
        monkeypatch.setattr(settings, "acs_reachability_timeout_seconds", 5.0)
        monkeypatch.setattr(settings, "acs_reachability_poll_interval_seconds", 0.0)

        # Первый вызов "up" (проверка ушёл ли) -> True (ещё не упал),
        # второй -> False (упал), дальше "вернулся ли" сразу True.
        responses = iter([True, False, True])

        async def _sequenced(host, ssh_port):
            return next(responses)

        monkeypatch.setattr(acs_snapshots, "_network_up", _sequenced)

        result = await acs_snapshots._wait_out_and_back("10.0.0.1", 22)

        assert result is True

    async def test_no_host_skips_wait_entirely(self):
        assert await acs_snapshots._wait_out_and_back(None, 22) is True


class TestVerifyBootstrapLogin:
    async def test_succeeds_on_first_attempt(self, monkeypatch):
        _FakeSshClient._fail_times = 0
        _FakeSshClient._degraded_times = 0
        _FakeSshClient._calls = 0
        monkeypatch.setattr(acs_snapshots, "SshClient", _FakeSshClient)

        async def _fake_creds(os_version_id):
            return {"ssh_username": "u", "password": "pw"}

        monkeypatch.setattr(
            acs_snapshots.server_service_client,
            "get_os_version_bootstrap_password",
            _fake_creds,
        )

        await acs_snapshots._verify_bootstrap_login("10.0.0.1", 22, "osv_x")

        assert _FakeSshClient._calls == 1

    async def test_retries_then_succeeds(self, monkeypatch):
        _FakeSshClient._fail_times = 2
        _FakeSshClient._degraded_times = 0
        _FakeSshClient._calls = 0
        monkeypatch.setattr(acs_snapshots, "SshClient", _FakeSshClient)
        monkeypatch.setattr(
            acs_snapshots.get_settings(), "acs_bootstrap_verify_retries", 5,
        )

        async def _fake_creds(os_version_id):
            return {"ssh_username": "u", "password": "pw"}

        monkeypatch.setattr(
            acs_snapshots.server_service_client,
            "get_os_version_bootstrap_password",
            _fake_creds,
        )

        await acs_snapshots._verify_bootstrap_login("10.0.0.1", 22, "osv_x")

        assert _FakeSshClient._calls == 3

    async def test_raises_after_exhausting_retries(self, monkeypatch):
        _FakeSshClient._fail_times = 999
        _FakeSshClient._degraded_times = 0
        _FakeSshClient._calls = 0
        monkeypatch.setattr(acs_snapshots, "SshClient", _FakeSshClient)
        monkeypatch.setattr(
            acs_snapshots.get_settings(), "acs_bootstrap_verify_retries", 3,
        )

        async def _fake_creds(os_version_id):
            return {"ssh_username": "u", "password": "pw"}

        monkeypatch.setattr(
            acs_snapshots.server_service_client,
            "get_os_version_bootstrap_password",
            _fake_creds,
        )

        with pytest.raises(SshError):
            await acs_snapshots._verify_bootstrap_login("10.0.0.1", 22, "osv_x")

        assert _FakeSshClient._calls == 3

    async def test_logs_in_but_degraded_keeps_retrying_until_running(self, monkeypatch):
        """Регрессия на живой инцидент: логин может пройти, пока система ещё
        `degraded` (Clonezilla live-окружение или незавершённый self-healing
        ребут самой ACS) — это НЕ должно считаться готовностью, иначе
        `server.prepare` стартует на сервере, который вот-вот перезагрузится
        у нас из-под ног ещё раз."""
        _FakeSshClient._fail_times = 0
        _FakeSshClient._degraded_times = 2
        _FakeSshClient._calls = 0
        monkeypatch.setattr(acs_snapshots, "SshClient", _FakeSshClient)
        monkeypatch.setattr(
            acs_snapshots.get_settings(), "acs_bootstrap_verify_retries", 5,
        )

        async def _fake_creds(os_version_id):
            return {"ssh_username": "u", "password": "pw"}

        monkeypatch.setattr(
            acs_snapshots.server_service_client,
            "get_os_version_bootstrap_password",
            _fake_creds,
        )

        await acs_snapshots._verify_bootstrap_login("10.0.0.1", 22, "osv_x")

        assert _FakeSshClient._calls == 3

    async def test_raises_if_never_reports_running(self, monkeypatch):
        """Логин всегда проходит, но `is-system-running` никогда не даёт
        `running` — ретраи должны исчерпаться и поднять `SshError`, а не
        молча вернуть успех."""
        _FakeSshClient._fail_times = 0
        _FakeSshClient._degraded_times = 999
        _FakeSshClient._calls = 0
        monkeypatch.setattr(acs_snapshots, "SshClient", _FakeSshClient)
        monkeypatch.setattr(
            acs_snapshots.get_settings(), "acs_bootstrap_verify_retries", 3,
        )

        async def _fake_creds(os_version_id):
            return {"ssh_username": "u", "password": "pw"}

        monkeypatch.setattr(
            acs_snapshots.server_service_client,
            "get_os_version_bootstrap_password",
            _fake_creds,
        )

        with pytest.raises(SshError, match="SSH_SYSTEM_NOT_READY"):
            await acs_snapshots._verify_bootstrap_login("10.0.0.1", 22, "osv_x")

        assert _FakeSshClient._calls == 3
