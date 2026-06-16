"""`audit_client.emit` пробрасывает опциональный `department_name` в payload.

Поле строго опциональное: при значении оно попадает в JSON рядом с
`department_id`, при None — не пишется (как и остальные пустые опционалы,
чтобы не раздувать payload).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.services import audit_client


def _settings_with_key():
    return type("S", (), {
        "logging_service_url": "http://logging.test",
        "logging_service_api_key": "test-key",
    })()


async def _emit_and_capture(**emit_kwargs) -> dict:
    captured: dict = {}

    async def fake_post(url, json, headers):
        captured.update(json)
        return MagicMock(status_code=200)

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=fake_post)

    with patch.object(audit_client, "get_settings", _settings_with_key), \
            patch.object(audit_client, "get_audit_client", lambda: mock_client):
        await audit_client.emit("server.power_on", **emit_kwargs)
    return captured


async def test_emit_includes_department_name_when_provided():
    payload = await _emit_and_capture(
        department_id="dep_1", department_name="Dept One",
    )
    assert payload["department_id"] == "dep_1"
    assert payload["department_name"] == "Dept One"


async def test_emit_omits_department_name_when_none():
    payload = await _emit_and_capture(department_id="dep_1")
    assert payload["department_id"] == "dep_1"
    assert "department_name" not in payload
