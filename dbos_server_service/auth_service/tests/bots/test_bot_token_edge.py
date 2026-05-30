"""Bot-token create edge cases через HTTP `/bots/{id}/tokens`.

Покрывает валидацию `expires_at`, которая раньше срабатывала только в
unit-тестах сервиса — здесь именно HTTP-путь, чтобы выловить регрессию
маршрута/схемы.
"""

from datetime import datetime, timedelta, timezone

BOTS_URL = "/api/auth/v1/bots"


async def _make_bot(client, admin_token, dept_id, name="te_bot"):
    resp = await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": []},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["bot_id"]


class TestBotTokenExpiresAtPast:
    """`expires_at` в прошлом → 422 INVALID_TOKEN_EXPIRY (не 500, не молчаливый успех)."""

    async def test_past_expires_at_rejected(self, client, admin_token, dept_a):
        bot_id = await _make_bot(client, admin_token, dept_a.id, name="past_exp_bot")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        resp = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "past_tok", "expires_at": past},
        )
        # Сервис кидает DomainValidationError → 422 + INVALID_TOKEN_EXPIRY.
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error_code"] == "INVALID_TOKEN_EXPIRY"
        # details должны нести то значение, что мы прислали (для отладки клиента).
        assert "expires_at" in body.get("details", {})

    async def test_now_expires_at_rejected(self, client, admin_token, dept_a):
        """`expires_at == now` — граница тоже отбивается (строгое >, не >=)."""
        bot_id = await _make_bot(client, admin_token, dept_a.id, name="now_exp_bot")
        # Берём слегка прошедший момент, чтобы не зависеть от микросекунд между
        # клиентом и сервером; гарантированно <= utcnow на серверной стороне.
        now_ish = (datetime.now(timezone.utc) - timedelta(milliseconds=10)).isoformat()

        resp = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "now_tok", "expires_at": now_ish},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "INVALID_TOKEN_EXPIRY"


class TestBotTokenNaiveUtcNormalize:
    """`expires_at` без tzinfo принимается, трактуется как UTC, сохраняется как UTC.

    Pydantic парсит ISO-строку с offset; чтобы получить naive-datetime в
    сервисе, шлём ISO без `Z`/`+00:00`. Сервис должен заменить на UTC, а не
    сравнить naive vs aware (TypeError → 500).
    """

    async def test_naive_iso_normalized_to_utc(self, client, admin_token, dept_a):
        bot_id = await _make_bot(client, admin_token, dept_a.id, name="naive_exp_bot")
        # `2026-12-31T23:59:59` — без `Z`, pydantic такое отдаёт как naive datetime.
        future_naive = (datetime.utcnow() + timedelta(days=7)).replace(microsecond=0)
        future_str = future_naive.isoformat()  # без `+00:00`

        resp = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "naive_tok", "expires_at": future_str},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Ответ возвращается уже с tzinfo (поскольку Session ORM хранит TZ-aware).
        returned = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
        # Тот же момент во времени, что и future_naive, но aware UTC.
        expected_aware = future_naive.replace(tzinfo=timezone.utc)
        delta = abs((returned - expected_aware).total_seconds())
        assert delta < 2, (
            f"naive ISO должен интерпретироваться как UTC; "
            f"returned={returned}, expected={expected_aware}, drift={delta}"
        )

    async def test_naive_past_expires_at_still_rejected(
        self, client, admin_token, dept_a,
    ):
        """naive past → нормализуется до UTC, всё равно отбивается INVALID_TOKEN_EXPIRY.

        Это закрывает баг: если сервис сравнил naive vs aware datetime до
        normalize'а, упал бы TypeError → 500. Должно быть 422.
        """
        bot_id = await _make_bot(client, admin_token, dept_a.id, name="naive_past_bot")
        past_naive = (datetime.utcnow() - timedelta(days=1)).replace(microsecond=0)

        resp = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "naive_past_tok", "expires_at": past_naive.isoformat()},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "INVALID_TOKEN_EXPIRY"
