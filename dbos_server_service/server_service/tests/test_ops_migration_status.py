"""Тесты `/api/server/v1/internal/migration_status` под s2s-канал.

ops-роут отдельный от worker'ского `/internal/secrets/migration_status`:
* worker_bot ходит сюда через JWT/PAT и матрицу `entity_permissions`;
* rotation_runner и другие s2s-runner'ы — через shared-secret из
  `SERVER_INBOUND_SERVICE_API_KEYS`, под header'ом `X-Service-Identity`.

Скрипт `scripts/k8s/rotate_master_key.sh --auto-finalize` дёргает этот route,
чтобы дождаться `outbox.pending == 0` + `remaining_legacy_total == 0` перед
drop'ом старого `SERVER_ENCRYPTION_KEY__v<old>`. Без него скрипт ловил 404
на `/migration_status` или 403 на `/secrets/migration_status` (worker scope).
"""

from __future__ import annotations

import pytest

from src.core.config import get_settings

OPS_PATH = "/api/server/v1/internal/migration_status"
ROTATION_RUNNER_SECRET = "test-rotation-runner-secret-do-not-use-in-prod"


@pytest.fixture
def configure_rotation_runner(monkeypatch):
    """Сконфигурировать `service_api_keys[rotation_runner]` через env.

    `get_settings()` lru_cached'ится — чистим cache до и после теста, чтобы
    значения не утекали между кейсами.
    """
    from src.core.config import get_settings as _get_settings

    monkeypatch.setenv(
        "SERVER_INBOUND_SERVICE_API_KEYS",
        f"rotation_runner={ROTATION_RUNNER_SECRET}",
    )
    _get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    _get_settings.cache_clear()  # type: ignore[attr-defined]


def _ops_headers(token: str, identity: str = "rotation_runner") -> dict[str, str]:
    """Authorization Bearer + X-Service-Identity, как в rotate_master_key.sh."""
    return {
        "Authorization": f"Bearer {token}",
        "X-Service-Identity": identity,
    }


class TestOpsMigrationStatus:
    async def test_rotation_runner_with_correct_secret_passes(
        self, client, configure_rotation_runner,
    ):
        """rotation_runner с правильным bearer'ом получает 200 + payload."""
        resp = await client.get(
            OPS_PATH, headers=_ops_headers(ROTATION_RUNNER_SECRET),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Контракт `MigrationStatusResponse` — те же поля, что для worker'а;
        # rotate-runner смотрит на `remaining_legacy_total` и `outbox.pending`.
        for key in (
            "remaining_legacy_total",
            "outbox_pending",
            "active_version",
            "server_account_password_encrypted",
            "ipmi_controller_password_encrypted",
        ):
            assert key in body, f"missing field {key}: {body}"
        assert body["outbox"]["pending"] == 0
        assert body["remaining_legacy_total"] == 0

    async def test_missing_identity_header_returns_401(
        self, client, configure_rotation_runner,
    ):
        """Без `X-Service-Identity` — 401 SERVICE_IDENTITY_REQUIRED."""
        resp = await client.get(
            OPS_PATH,
            headers={"Authorization": f"Bearer {ROTATION_RUNNER_SECRET}"},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_REQUIRED"

    async def test_unknown_identity_returns_403(
        self, client, configure_rotation_runner,
    ):
        """Identity вне whitelist'а — 403 SERVICE_IDENTITY_NOT_ALLOWED."""
        resp = await client.get(
            OPS_PATH,
            headers=_ops_headers(ROTATION_RUNNER_SECRET, identity="some_other_runner"),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_NOT_ALLOWED"

    async def test_missing_bearer_returns_401(
        self, client, configure_rotation_runner,
    ):
        resp = await client.get(
            OPS_PATH, headers={"X-Service-Identity": "rotation_runner"},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_SERVICE_TOKEN"

    async def test_wrong_bearer_returns_401(
        self, client, configure_rotation_runner,
    ):
        resp = await client.get(
            OPS_PATH, headers=_ops_headers("not-the-real-secret"),
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_SERVICE_TOKEN"

    async def test_identity_not_configured_returns_401(
        self, client, monkeypatch,
    ):
        """Identity в whitelist'е route'а, но ключа нет в env → 401.

        Отдаём тот же `INVALID_SERVICE_TOKEN`, что и для wrong-bearer'а:
        иначе разница 401 позволяла бы перечислить configured-identity.
        """
        from src.core.config import get_settings as _get_settings
        monkeypatch.delenv("SERVER_INBOUND_SERVICE_API_KEYS", raising=False)
        monkeypatch.delenv("SERVICE_API_KEYS", raising=False)
        _get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            resp = await client.get(
                OPS_PATH, headers=_ops_headers("anything"),
            )
            assert resp.status_code == 401, resp.text
            assert resp.json()["error_code"] == "INVALID_SERVICE_TOKEN"
        finally:
            _get_settings.cache_clear()  # type: ignore[attr-defined]

    async def test_user_jwt_does_not_pass_ops_route(
        self, client, admin_token, configure_rotation_runner,
    ):
        """User-JWT (admin) тоже не должен ходить в ops-route — нет identity header'а."""
        resp = await client.get(
            OPS_PATH, headers={"Authorization": f"Bearer {admin_token}"},
        )
        # Header'а identity нет → 401 SERVICE_IDENTITY_REQUIRED, не 200.
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_REQUIRED"

    async def test_ops_path_not_in_openapi(self, client):
        """Ops-route скрыт из публичного OpenAPI — runner'ы не должны утекать в каталог."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert OPS_PATH not in paths, (
            f"ops route must be hidden from OpenAPI, found {OPS_PATH}"
        )


class TestServiceApiKeysParser:
    """Парсинг `SERVER_INBOUND_SERVICE_API_KEYS` из разных env-форматов."""

    def test_kv_list_parses(self, monkeypatch):
        from src.core.config import Settings, get_settings
        monkeypatch.setenv(
            "SERVER_INBOUND_SERVICE_API_KEYS",
            "rotation_runner=secret_a, other=secret_b",
        )
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            s = Settings()
            assert s.service_api_keys == {
                "rotation_runner": "secret_a",
                "other": "secret_b",
            }
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]

    def test_json_parses(self, monkeypatch):
        from src.core.config import Settings, get_settings
        monkeypatch.setenv(
            "SERVER_INBOUND_SERVICE_API_KEYS",
            '{"rotation_runner":"jsec","other":"other_sec"}',
        )
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            s = Settings()
            assert s.service_api_keys == {
                "rotation_runner": "jsec",
                "other": "other_sec",
            }
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]

    def test_empty_value_yields_empty_dict(self, monkeypatch):
        from src.core.config import Settings, get_settings
        monkeypatch.setenv("SERVER_INBOUND_SERVICE_API_KEYS", "")
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            s = Settings()
            assert s.service_api_keys == {}
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]

    def test_missing_equals_in_kv_raises(self, monkeypatch):
        from src.core.config import Settings, get_settings
        monkeypatch.setenv("SERVER_INBOUND_SERVICE_API_KEYS", "no_equals_here")
        get_settings.cache_clear()  # type: ignore[attr-defined]
        try:
            with pytest.raises(Exception):
                Settings()
        finally:
            get_settings.cache_clear()  # type: ignore[attr-defined]
