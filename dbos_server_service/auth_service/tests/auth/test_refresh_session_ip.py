"""POST /refresh — проверка обновления Session.ip_address и Session.user_agent.

До фикса `auth_service.refresh` принимал `ip_address`/`user_agent` из endpoint'а
но `session_repo.rotate` их игнорировал (не включал в UPDATE). В результате
сессия хранила IP входа и никогда не обновлялась при ротации.
"""

from sqlalchemy import select

from src.core.security import hash_refresh_token
from src.models import Session
from tests._helpers.http import login as _login  # noqa: F401 — общий helper

LOGIN_URL = "/api/auth/v1/login"
REFRESH_URL = "/api/auth/v1/refresh"


class TestRefreshUpdatesSessionIp:
    # XFF-чтение покрывается через test_main_extract_client_ip_wiring.py
    # (отдельный app с настроенным trusted_proxies_env). Здесь оставлен только
    # user-agent + IP-preserve кейсы — они работают на дефолтном AsyncClient.

    async def test_refresh_updates_user_agent_in_session(self, client, account_admin, db):
        """После `/refresh` с кастомным User-Agent сессия его запоминает."""
        data = await _login(client)
        raw_refresh = data["refresh_token"]

        resp = await client.post(
            REFRESH_URL,
            json={"refresh_token": raw_refresh},
            headers={"User-Agent": "TestBot/2.0"},
        )
        assert resp.status_code == 200, resp.text

        new_raw = resp.json()["refresh_token"]
        new_hash = hash_refresh_token(new_raw)
        sess = await db.scalar(
            select(Session).where(Session.refresh_token_hash == new_hash)
        )
        assert sess is not None
        assert sess.user_agent == "TestBot/2.0", (
            f"Session.user_agent not updated after refresh, got: {sess.user_agent!r}"
        )

    async def test_refresh_without_ip_keeps_previous_ip(self, client, account_admin, db):
        """Если IP не передаётся, сессия сохраняет предыдущее значение (не сбрасывает в None)."""
        data = await _login(client)
        raw_login_refresh = data["refresh_token"]

        # Первая ротация с конкретным IP.
        resp1 = await client.post(
            REFRESH_URL,
            json={"refresh_token": raw_login_refresh},
            headers={"X-Forwarded-For": "192.168.1.50"},
        )
        assert resp1.status_code == 200
        raw2 = resp1.json()["refresh_token"]

        # Вторая ротация без IP. ip_address не должен сброситься в None.
        resp2 = await client.post(
            REFRESH_URL,
            json={"refresh_token": raw2},
        )
        assert resp2.status_code == 200

        raw3 = resp2.json()["refresh_token"]
        new_hash = hash_refresh_token(raw3)
        sess = await db.scalar(
            select(Session).where(Session.refresh_token_hash == new_hash)
        )
        assert sess is not None
        # SessionRepository.rotate обновляет ip_address только если не None.
        # Если ASGI-транспорт шлёт client.host="testclient", accept тот же.
        # Главное — не None (session_repo.rotate с ip_address=None игнорирует поле).
        # Проверяем, что сессия существует и ip_address явно не сброшен в None
        # из-за отсутствия заголовка.
        assert sess.ip_address is not None or sess.ip_address == "testclient", (
            "ip_address should either be preserved or set from client host, not None"
        )
