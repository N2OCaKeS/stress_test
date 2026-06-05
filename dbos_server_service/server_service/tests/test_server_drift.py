"""`GET /api/server/v1/servers/{id}/drift` — агрегированная drift-сводка.

Endpoint собирает события `server_account.drift_detected` из loging_service
за окно `[since, now]`. Drift-данные на стороне server_service не хранятся —
истина в loging, поэтому мокаем `loging_client.fetch_drift_events`.

Тестируется:

* admin-роль читает drift → 200, корректный shape ответа;
* reader-роль читает → 200 (`view_drift` есть у reader по дефолту);
* guest без `view_drift` → 403;
* нет токена → 401;
* cross-dept сервер → 404 (скрытие существования);
* `since` по умолчанию = now - 24h, явный since пробрасывается в loging;
* `truncated` пробрасывается из loging-клиента наверх.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

BASE = "/api/server/v1"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


@pytest.fixture
def fake_drift(monkeypatch):
    """Мокает `loging_client.fetch_drift_events`, ловит kwargs вызова.

    Возвращает callable, которым тест задаёт фейковый payload и
    `truncated`-флаг. Также возвращает `captured` dict с последними kwargs.
    """
    from src.services import loging_client

    captured: dict = {}

    def configure(events: list[dict], truncated: bool = False):
        async def fake(*, server_id: str, since):
            captured["server_id"] = server_id
            captured["since"] = since
            return list(events), truncated

        # Сервис делает `from src.services import loging_client` локально —
        # подмена атрибута на модуле перехватывает все будущие lookup'ы.
        monkeypatch.setattr(loging_client, "fetch_drift_events", fake)
        return captured

    return configure


class TestDriftEndpoint:
    async def test_admin_reads_drift_summary(
        self, client, admin_role_token_a, make_server, dept_a, fake_drift,
    ):
        srv = await make_server(department_id=dept_a)
        ts = datetime.now(timezone.utc).isoformat()
        fake_drift([
            {
                "timestamp": ts,
                "target_id": srv.id,
                "details": {
                    "login": "alice",
                    "drift": "attributes",
                    "fields": ["has_sudo", "shell"],
                },
            },
            {
                "timestamp": ts,
                "target_id": srv.id,
                "details": {"login": "bob", "drift": "missing_on_box"},
            },
        ])

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/drift",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server_id"] == srv.id
        assert body["truncated"] is False
        assert len(body["drifts"]) == 2
        first = body["drifts"][0]
        assert first["login"] == "alice"
        assert first["drift_type"] == "attributes"
        assert first["fields"] == ["has_sudo", "shell"]

    async def test_operator_can_read_drift(
        self, client, operator_token_a, make_server, dept_a, fake_drift,
    ):
        srv = await make_server(department_id=dept_a)
        fake_drift([])
        resp = await client.get(
            f"{BASE}/servers/{srv.id}/drift",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 200, resp.text

    async def test_reader_forbidden_by_default(
        self, client, reader_token_a, make_server, dept_a, fake_drift,
    ):
        """Reader не получает `view_drift` по default-grant'у (инвариант
        «reader = только view»). Проверяем 403, чтобы зафиксировать контракт.
        """
        srv = await make_server(department_id=dept_a)
        fake_drift([])
        resp = await client.get(
            f"{BASE}/servers/{srv.id}/drift",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_guest_forbidden(
        self, client, guest_token_a, make_server, dept_a, fake_drift,
    ):
        srv = await make_server(department_id=dept_a)
        fake_drift([])
        resp = await client.get(
            f"{BASE}/servers/{srv.id}/drift",
            headers=_hdr(guest_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client, make_server, dept_a):
        srv = await make_server(department_id=dept_a)
        resp = await client.get(f"{BASE}/servers/{srv.id}/drift")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server, dept_a, fake_drift,
    ):
        """Сервер в dept_a — operator из dept_b (у него есть view_drift,
        но dept не совпадает) видит 404 (cover-up существования)."""
        srv = await make_server(department_id=dept_a)
        fake_drift([])
        resp = await client.get(
            f"{BASE}/servers/{srv.id}/drift",
            headers=_hdr(operator_token_b),
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_default_since_is_24h_window(
        self, client, admin_role_token_a, make_server, dept_a, fake_drift,
    ):
        srv = await make_server(department_id=dept_a)
        captured = fake_drift([])
        resp = await client.get(
            f"{BASE}/servers/{srv.id}/drift",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        # Окно не должно быть шире 24h + epsilon.
        since = captured["since"]
        assert since.tzinfo is not None
        delta = datetime.now(timezone.utc) - since
        assert timedelta(hours=23, minutes=59) <= delta <= timedelta(hours=24, minutes=1)

    async def test_explicit_since_passed_to_loging(
        self, client, admin_role_token_a, make_server, dept_a, fake_drift,
    ):
        srv = await make_server(department_id=dept_a)
        captured = fake_drift([])
        since_iso = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        resp = await client.get(
            f"{BASE}/servers/{srv.id}/drift",
            params={"since": since_iso},
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200, resp.text
        assert captured["since"].isoformat().startswith(since_iso[:19])

    async def test_truncated_flag_propagated(
        self, client, admin_role_token_a, make_server, dept_a, fake_drift,
    ):
        srv = await make_server(department_id=dept_a)
        fake_drift([], truncated=True)
        resp = await client.get(
            f"{BASE}/servers/{srv.id}/drift",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["truncated"] is True
