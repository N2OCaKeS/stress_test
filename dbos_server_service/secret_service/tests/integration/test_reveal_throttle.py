"""Reveal-throttle: первый reveal → CRITICAL; 5-min окно даёт INFO `_throttled`.

README §«Reveal + throttle». Окно сбрасывается за пределами 5 минут — в
интеграционных тестах эмулируем «выход из окна» сбросом in-memory state'а
(или скачком monotonic-времени — но проще сбросить, throttle хранит monotonic
clock внутри `_inmem_state`).
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import auth_header

BASE = "/api/secret/v1"

pytestmark = pytest.mark.asyncio


async def _make_cred(client, identity_factory) -> tuple[str, str]:
    owner = identity_factory(
        user_id="usr_throttle_owner",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )
    resp = await client.post(
        f"{BASE}/credentials",
        headers=auth_header(owner),
        json={
            "name": "throttle_target",
            "service": "jira",
            "scope": "personal",
            "secret": "throttle-secret",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], owner


async def test_first_reveal_critical_subsequent_throttled(
    client, identity_factory, mock_logging_service,
):
    cred_id, owner = await _make_cred(client, identity_factory)

    for _ in range(5):
        resp = await client.post(
            f"{BASE}/credentials/{cred_id}/reveal", headers=auth_header(owner),
        )
        assert resp.status_code == 200

    revealed = mock_logging_service.by_action("tokens.revealed")
    throttled = mock_logging_service.by_action("tokens.revealed_throttled")

    # Первый reveal — ровно один CRITICAL.
    assert len(revealed) == 1
    assert revealed[0]["severity"] is None or revealed[0]["severity"] == "CRITICAL"
    # Дефолтная severity подтягивается ниже по стеку — здесь severity=None,
    # потому что caller не передал; default_severity() в реальном emit()
    # резолвит, но мы перехватываем raw. Поэтому проверяем что hint есть
    # на success в каталоге.
    from src.services.audit_events import default_severity
    assert default_severity("tokens.revealed", "success") == "CRITICAL"

    # Остальные 4 — INFO throttled, count = 2,3,4,5.
    assert len(throttled) == 4
    counts = [e["details"]["count"] for e in throttled]
    assert counts == [2, 3, 4, 5]
    assert default_severity("tokens.revealed_throttled", "success") == "INFO"


async def test_new_window_after_state_reset(
    client, identity_factory, mock_logging_service,
):
    """Эмулируем «выход из окна»: сбрасываем in-memory throttle state.
    Реальный wall-clock test с 5-минутной паузой нелепо долгий."""
    from src.services import reveal_throttle

    cred_id, owner = await _make_cred(client, identity_factory)

    first = await client.post(
        f"{BASE}/credentials/{cred_id}/reveal", headers=auth_header(owner),
    )
    assert first.status_code == 200

    # Эмуляция «прошло >5 мин».
    reveal_throttle._reset_for_tests()

    second = await client.post(
        f"{BASE}/credentials/{cred_id}/reveal", headers=auth_header(owner),
    )
    assert second.status_code == 200

    revealed = mock_logging_service.by_action("tokens.revealed")
    # Каждый «новый window» порождает ещё один CRITICAL revealed.
    assert len(revealed) == 2
