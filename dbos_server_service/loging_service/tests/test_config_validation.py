"""Тесты production-guard для Settings (loging_service).

Закрывает дыру: дефолтный SERVICE_API_KEY='change-me-service-key' при
APP_ENV=production раньше принимался → любой пользователь сети мог писать
аудит-события от имени любого сервиса (включая loging_service с retention-
инвариантом).
"""

import pytest
from pydantic import ValidationError


def _load_settings_fresh(monkeypatch, env: dict[str, str]):
    """Сбрасывает lru_cache get_settings и грузит Settings() с заданным env.

    Возвращает экземпляр Settings либо пробрасывает ValidationError.
    """
    # Подавляем .env-файл, чтобы не было локального шума.
    monkeypatch.setenv("APP_ENV", env.get("APP_ENV", "local"))
    for k in (
        "SERVICE_API_KEY",
        "SERVICE_APP_KEYS",
        "SERVICE_API_KEYS",
        "INTROSPECT_SERVICE_API_KEY",
        "APP_DEBUG",
        "AUTH_SERVICE_URL",
        "INTROSPECT_TLS_VERIFY",
    ):
        if k in env:
            monkeypatch.setenv(k, env[k])
        else:
            monkeypatch.delenv(k, raising=False)

    from src.core import config as cfg
    cfg.get_settings.cache_clear()
    # Игнорируем .env, передавая _env_file=None, чтобы тесты были
    # детерминированными независимо от рабочего каталога.
    return cfg.Settings(_env_file=None)


class TestProductionGuard:
    def test_default_service_api_key_rejected_in_production(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch,
                {
                    "APP_ENV": "production",
                    "SERVICE_API_KEY": "change-me-service-key",
                    "APP_DEBUG": "false",
                },
            )
        assert "SERVICE_API_KEY" in str(excinfo.value)

    def test_empty_service_api_key_rejected_in_production(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch,
                {
                    "APP_ENV": "production",
                    "SERVICE_API_KEY": "",
                    "APP_DEBUG": "false",
                },
            )
        assert "SERVICE_API_KEY" in str(excinfo.value)

    def test_app_debug_true_rejected_in_production(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch,
                {
                    "APP_ENV": "production",
                    "SERVICE_API_KEY": "real-secret-xyz-1234567890",
                    "APP_DEBUG": "true",
                },
            )
        assert "APP_DEBUG" in str(excinfo.value)

    def test_production_with_real_key_accepted(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "production",
                "SERVICE_API_KEY": "real-secret-xyz-1234567890",
                "APP_DEBUG": "false",
            },
        )
        assert s.app_env == "production"
        assert s.service_api_key == "real-secret-xyz-1234567890"
        assert s.app_debug is False


class TestNonProductionEnvs:
    """Вне production дефолтные значения остаются допустимыми (dev / test / local)."""

    def test_local_with_default_key_ok(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "local",
                "SERVICE_API_KEY": "change-me-service-key",
            },
        )
        assert s.service_api_key == "change-me-service-key"

    def test_development_with_default_key_ok(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "development",
                "SERVICE_API_KEY": "change-me-service-key",
            },
        )
        assert s.service_api_key == "change-me-service-key"

    def test_test_env_with_default_key_ok(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "test",
                "SERVICE_API_KEY": "change-me-service-key",
            },
        )
        assert s.service_api_key == "change-me-service-key"


class TestAuthServiceUrlHttpsGuard:
    """В production AUTH_SERVICE_URL должен быть https:// — иначе MITM в
    кластере может подменить ответ introspect и выдать loging_admin кому угодно.

    Исключение: localhost / 127.0.0.1 / ::1 (devcontainer-сценарии, TLS-on-host).
    """

    _PROD_KEY = "real-secret-xyz-1234567890"

    def test_production_http_remote_rejected(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch,
                {
                    "APP_ENV": "production",
                    "SERVICE_API_KEY": self._PROD_KEY,
                    "APP_DEBUG": "false",
                    "AUTH_SERVICE_URL": "http://auth:8000",
                },
            )
        assert "AUTH_SERVICE_URL" in str(excinfo.value)
        assert "https" in str(excinfo.value)

    def test_production_http_localhost_accepted(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "production",
                "SERVICE_API_KEY": self._PROD_KEY,
                "APP_DEBUG": "false",
                "AUTH_SERVICE_URL": "http://localhost:8000",
            },
        )
        assert s.auth_service_url == "http://localhost:8000"

    def test_production_http_127_0_0_1_accepted(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "production",
                "SERVICE_API_KEY": self._PROD_KEY,
                "APP_DEBUG": "false",
                "AUTH_SERVICE_URL": "http://127.0.0.1:8000",
            },
        )
        assert s.auth_service_url == "http://127.0.0.1:8000"

    def test_production_https_accepted(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "production",
                "SERVICE_API_KEY": self._PROD_KEY,
                "APP_DEBUG": "false",
                "AUTH_SERVICE_URL": "https://auth.example.com",
            },
        )
        assert s.auth_service_url == "https://auth.example.com"

    def test_production_no_auth_url_accepted(self, monkeypatch):
        """AUTH_SERVICE_URL может быть пустым — `_fetch_identity` отдельно
        вернёт 503 AUTH_SERVICE_NOT_CONFIGURED, но Settings-validator
        не должен валиться на отсутствующем URL."""
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "production",
                "SERVICE_API_KEY": self._PROD_KEY,
                "APP_DEBUG": "false",
            },
        )
        assert s.auth_service_url is None

    def test_non_prod_http_remote_accepted(self, monkeypatch):
        """В dev/test/local plain http:// разрешён (docker-compose сценарии)."""
        for env in ("local", "development", "test"):
            s = _load_settings_fresh(
                monkeypatch,
                {
                    "APP_ENV": env,
                    "AUTH_SERVICE_URL": "http://auth:8000",
                },
            )
            assert s.auth_service_url == "http://auth:8000", f"failed for APP_ENV={env}"


class TestIntrospectTlsVerifyProductionGuard:
    """В production с https:// AUTH_SERVICE_URL — INTROSPECT_TLS_VERIFY=false
    запрещён. https+verify=False тихо отключает chain-of-trust → MITM может
    выдать loging_admin кому угодно подменой introspect-ответа.

    Исключения: localhost / 127.0.0.1 / ::1 (devcontainer self-signed).
    """

    _PROD_KEY = "real-secret-xyz-1234567890"

    def test_prod_https_remote_verify_false_rejected(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch,
                {
                    "APP_ENV": "production",
                    "SERVICE_API_KEY": self._PROD_KEY,
                    "APP_DEBUG": "false",
                    "AUTH_SERVICE_URL": "https://auth.cluster.svc:8000",
                    "INTROSPECT_TLS_VERIFY": "false",
                },
            )
        assert "INTROSPECT_TLS_VERIFY" in str(excinfo.value)

    def test_prod_https_remote_verify_true_accepted(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "production",
                "SERVICE_API_KEY": self._PROD_KEY,
                "APP_DEBUG": "false",
                "AUTH_SERVICE_URL": "https://auth.cluster.svc:8000",
                "INTROSPECT_TLS_VERIFY": "true",
            },
        )
        assert s.introspect_tls_verify is True

    def test_prod_https_localhost_verify_false_accepted(self, monkeypatch):
        """Localhost-исключение: self-signed devcontainer-сценарий."""
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "production",
                "SERVICE_API_KEY": self._PROD_KEY,
                "APP_DEBUG": "false",
                "AUTH_SERVICE_URL": "https://localhost:8000",
                "INTROSPECT_TLS_VERIFY": "false",
            },
        )
        assert s.introspect_tls_verify is False

    def test_prod_no_auth_url_verify_false_accepted(self, monkeypatch):
        """Без AUTH_SERVICE_URL guard'у нечего проверять — verify=False допустим
        (introspect-вызовы всё равно вернут 503 AUTH_SERVICE_NOT_CONFIGURED).
        """
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "production",
                "SERVICE_API_KEY": self._PROD_KEY,
                "APP_DEBUG": "false",
                "INTROSPECT_TLS_VERIFY": "false",
            },
        )
        assert s.introspect_tls_verify is False

    def test_non_prod_verify_false_accepted(self, monkeypatch):
        """В dev/test/local verify=False разрешён независимо от URL."""
        for env in ("local", "development", "test"):
            s = _load_settings_fresh(
                monkeypatch,
                {
                    "APP_ENV": env,
                    "AUTH_SERVICE_URL": "https://auth.cluster.svc:8000",
                    "INTROSPECT_TLS_VERIFY": "false",
                },
            )
            assert s.introspect_tls_verify is False, f"failed for APP_ENV={env}"


class TestIntrospectServiceApiKeyProductionGuard:
    """В production: если `SERVICE_API_KEYS` заполнен (per-service mode), то
    `INTROSPECT_SERVICE_API_KEY` обязан быть непустым — иначе `_fetch_identity`
    молча фолбэчит на shared `SERVICE_API_KEY`, что нивелирует ключевую
    гарантию per-service режима (изоляция ingest-ключей от introspect-ключа).
    """

    _PROD_KEY = "real-secret-xyz-1234567890"
    _PER_SERVICE_KEYS = (
        '{"auth_service":"k-auth-1234567890","server_service":"k-srv-1234567890"}'
    )

    @pytest.mark.parametrize(
        "service_api_keys, introspect_key, should_fail",
        [
            # per-service mode + пустой introspect_key → fail
            (_PER_SERVICE_KEYS, "", True),
            # per-service mode + заполненный introspect_key → ok
            (_PER_SERVICE_KEYS, "introspect-bearer-abcdef1234", False),
            # legacy single-key (SERVICE_API_KEYS пуст) + пустой introspect →
            # ok (fallback на SERVICE_API_KEY), guard не активируется
            ("", "", False),
        ],
    )
    def test_per_service_requires_introspect_key(
        self, monkeypatch, service_api_keys, introspect_key, should_fail
    ):
        env = {
            "APP_ENV": "production",
            "SERVICE_API_KEY": self._PROD_KEY,
            "APP_DEBUG": "false",
            "SERVICE_API_KEYS": service_api_keys,
            "INTROSPECT_SERVICE_API_KEY": introspect_key,
        }
        if should_fail:
            with pytest.raises(ValidationError) as excinfo:
                _load_settings_fresh(monkeypatch, env)
            assert "INTROSPECT_SERVICE_API_KEY" in str(excinfo.value)
            assert "SERVICE_API_KEYS" in str(excinfo.value)
        else:
            s = _load_settings_fresh(monkeypatch, env)
            assert s.introspect_service_api_key == introspect_key

    def test_non_prod_per_service_empty_introspect_accepted(self, monkeypatch):
        """Вне production гард не активируется (dev-стенды с одним shared key)."""
        for app_env in ("local", "development", "test"):
            s = _load_settings_fresh(
                monkeypatch,
                {
                    "APP_ENV": app_env,
                    "SERVICE_API_KEYS": self._PER_SERVICE_KEYS,
                    "INTROSPECT_SERVICE_API_KEY": "",
                },
            )
            assert s.introspect_service_api_key == ""
