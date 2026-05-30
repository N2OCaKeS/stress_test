"""Тесты production-guard для Settings (loging_service).

Legacy single-key режим (`SERVICE_API_KEY`) удалён: единственный канал
service-to-service ingest — `SERVICE_API_KEYS` JSON map. Production-guard'ы
обязывают: непустой map, отдельный `INTROSPECT_SERVICE_API_KEY`, https
для `AUTH_SERVICE_URL` (loopback исключение), `verify=true` на https-remote,
key-separation между introspect и любыми ingest-ключами.
"""

import pytest
from pydantic import ValidationError


_PER_SERVICE_KEYS = (
    '{"auth_service":"k-auth-1234567890","server_service":"k-srv-1234567890"}'
)
_INTROSPECT_KEY = "introspect-only-key-9876"
_AUTH_URL = "https://auth.example.com"


def _load_settings_fresh(monkeypatch, env: dict[str, str]):
    """Сбрасывает lru_cache get_settings и грузит Settings() с заданным env.

    Возвращает экземпляр Settings либо пробрасывает ValidationError.
    """
    monkeypatch.setenv("APP_ENV", env.get("APP_ENV", "local"))
    for k in (
        "SERVICE_API_KEY",  # удалён, но чистим — на случай шума из родительского env
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
    return cfg.Settings(_env_file=None)


def _prod_env(**overrides) -> dict[str, str]:
    """Базовый валидный production-env; тесты переопределяют отдельные ключи."""
    base = {
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "SERVICE_API_KEYS": _PER_SERVICE_KEYS,
        "INTROSPECT_SERVICE_API_KEY": _INTROSPECT_KEY,
        "AUTH_SERVICE_URL": _AUTH_URL,
    }
    base.update(overrides)
    return base


class TestProductionGuardServiceApiKeys:
    """В production `SERVICE_API_KEYS` обязан быть непустым JSON-объектом."""

    def test_empty_service_api_keys_rejected_in_production(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(monkeypatch, _prod_env(SERVICE_API_KEYS=""))
        assert "SERVICE_API_KEYS" in str(excinfo.value)

    def test_unset_service_api_keys_rejected_in_production(self, monkeypatch):
        env = _prod_env()
        del env["SERVICE_API_KEYS"]
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(monkeypatch, env)
        assert "SERVICE_API_KEYS" in str(excinfo.value)

    def test_app_debug_true_rejected_in_production(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(monkeypatch, _prod_env(APP_DEBUG="true"))
        assert "APP_DEBUG" in str(excinfo.value)

    def test_production_with_per_service_keys_accepted(self, monkeypatch):
        s = _load_settings_fresh(monkeypatch, _prod_env())
        assert s.app_env == "production"
        assert s.service_api_keys["auth_service"] == "k-auth-1234567890"


class TestNonProductionEnvs:
    """Вне production пустой map допустим (ingest всё равно вернёт 503)."""

    @pytest.mark.parametrize("app_env", ["local", "development", "test"])
    def test_empty_map_ok_outside_production(self, monkeypatch, app_env):
        s = _load_settings_fresh(monkeypatch, {"APP_ENV": app_env})
        assert s.service_api_keys == {}


class TestAuthServiceUrlHttpsGuard:
    """В production AUTH_SERVICE_URL должен быть https:// — иначе MITM в
    кластере может подменить ответ introspect и выдать loging_admin кому угодно.

    Исключение: localhost / 127.0.0.1 / ::1 (devcontainer-сценарии, TLS-on-host).
    """

    def test_production_http_remote_rejected(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch, _prod_env(AUTH_SERVICE_URL="http://auth:8000")
            )
        assert "AUTH_SERVICE_URL" in str(excinfo.value)
        assert "https" in str(excinfo.value)

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
    )
    def test_production_http_loopback_accepted(self, monkeypatch, url):
        s = _load_settings_fresh(monkeypatch, _prod_env(AUTH_SERVICE_URL=url))
        assert s.auth_service_url == url

    def test_production_https_accepted(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch, _prod_env(AUTH_SERVICE_URL="https://auth.example.com")
        )
        assert s.auth_service_url == "https://auth.example.com"

    @pytest.mark.parametrize("app_env", ["local", "development", "test"])
    def test_non_prod_http_remote_accepted(self, monkeypatch, app_env):
        """В dev/test/local plain http:// разрешён (docker-compose сценарии)."""
        s = _load_settings_fresh(
            monkeypatch,
            {"APP_ENV": app_env, "AUTH_SERVICE_URL": "http://auth:8000"},
        )
        assert s.auth_service_url == "http://auth:8000"


class TestIntrospectTlsVerifyProductionGuard:
    """В production с https:// AUTH_SERVICE_URL — INTROSPECT_TLS_VERIFY=false
    запрещён. https+verify=False тихо отключает chain-of-trust → MITM может
    выдать loging_admin кому угодно подменой introspect-ответа.

    Исключения: localhost / 127.0.0.1 / ::1 (devcontainer self-signed).
    """

    def test_prod_https_remote_verify_false_rejected(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch,
                _prod_env(
                    AUTH_SERVICE_URL="https://auth.cluster.svc:8000",
                    INTROSPECT_TLS_VERIFY="false",
                ),
            )
        assert "INTROSPECT_TLS_VERIFY" in str(excinfo.value)

    def test_prod_https_remote_verify_true_accepted(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            _prod_env(
                AUTH_SERVICE_URL="https://auth.cluster.svc:8000",
                INTROSPECT_TLS_VERIFY="true",
            ),
        )
        assert s.introspect_tls_verify is True

    def test_prod_https_localhost_verify_false_accepted(self, monkeypatch):
        """Localhost-исключение: self-signed devcontainer-сценарий."""
        s = _load_settings_fresh(
            monkeypatch,
            _prod_env(
                AUTH_SERVICE_URL="https://localhost:8000",
                INTROSPECT_TLS_VERIFY="false",
            ),
        )
        assert s.introspect_tls_verify is False

    def test_prod_http_localhost_verify_false_accepted(self, monkeypatch):
        """Plain-http localhost AUTH_SERVICE_URL: нет https-remote для MITM →
        verify=False допустим.
        """
        s = _load_settings_fresh(
            monkeypatch,
            _prod_env(
                AUTH_SERVICE_URL="http://localhost:8000",
                INTROSPECT_TLS_VERIFY="false",
            ),
        )
        assert s.introspect_tls_verify is False

    @pytest.mark.parametrize("app_env", ["local", "development", "test"])
    def test_non_prod_verify_false_accepted(self, monkeypatch, app_env):
        """В dev/test/local verify=False разрешён независимо от URL."""
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": app_env,
                "AUTH_SERVICE_URL": "https://auth.cluster.svc:8000",
                "INTROSPECT_TLS_VERIFY": "false",
            },
        )
        assert s.introspect_tls_verify is False


class TestIntrospectServiceApiKeyProductionGuard:
    """В production `INTROSPECT_SERVICE_API_KEY` обязан быть непустым — без
    него `_fetch_identity` возвращает 503 на любой introspect-вызов
    (легаси-фолбэк на shared ключ удалён).
    """

    def test_empty_introspect_key_rejected_in_production(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch, _prod_env(INTROSPECT_SERVICE_API_KEY="")
            )
        assert "INTROSPECT_SERVICE_API_KEY" in str(excinfo.value)

    def test_introspect_key_accepted_in_production(self, monkeypatch):
        s = _load_settings_fresh(
            monkeypatch,
            _prod_env(INTROSPECT_SERVICE_API_KEY="introspect-bearer-abcdef1234"),
        )
        assert s.introspect_service_api_key == "introspect-bearer-abcdef1234"

    @pytest.mark.parametrize("app_env", ["local", "development", "test"])
    def test_non_prod_empty_introspect_accepted(self, monkeypatch, app_env):
        """Вне production гард не активируется (dev-стенды с моками auth_service)."""
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": app_env,
                "SERVICE_API_KEYS": _PER_SERVICE_KEYS,
                "INTROSPECT_SERVICE_API_KEY": "",
            },
        )
        assert s.introspect_service_api_key == ""


class TestAuthServiceUrlRequiredInProduction:
    """В production AUTH_SERVICE_URL обязателен. Без него admin/reader-ручки
    не падают на старте, но `_fetch_identity` отдаёт 503 на каждом запросе —
    pod выглядит живым, а JWT-эндпоинты молча недоступны. Fail-fast на старте.
    """

    def test_production_without_auth_url_rejected(self, monkeypatch):
        env = _prod_env()
        del env["AUTH_SERVICE_URL"]
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(monkeypatch, env)
        assert "AUTH_SERVICE_URL" in str(excinfo.value)

    def test_production_with_https_auth_url_accepted(self, monkeypatch):
        s = _load_settings_fresh(monkeypatch, _prod_env())
        assert s.auth_service_url == _AUTH_URL

    @pytest.mark.parametrize("app_env", ["local", "development", "test"])
    def test_non_prod_without_auth_url_accepted(self, monkeypatch, app_env):
        """Вне production AUTH_SERVICE_URL остаётся опциональным."""
        s = _load_settings_fresh(monkeypatch, {"APP_ENV": app_env})
        assert s.auth_service_url is None


class TestIntrospectKeyCollisionGuard:
    """В production INTROSPECT_SERVICE_API_KEY не должен совпадать ни с одним
    значением SERVICE_API_KEYS — иначе утёкший ingest-ключ сразу открывает
    /introspect от имени loging_service (нарушение key-separation).
    """

    def test_collision_with_ingest_key_rejected(self, monkeypatch):
        with pytest.raises(ValidationError) as excinfo:
            _load_settings_fresh(
                monkeypatch,
                _prod_env(INTROSPECT_SERVICE_API_KEY="k-auth-1234567890"),
            )
        assert "INTROSPECT_SERVICE_API_KEY" in str(excinfo.value)
        assert "distinct" in str(excinfo.value)

    def test_distinct_introspect_key_accepted(self, monkeypatch):
        s = _load_settings_fresh(monkeypatch, _prod_env())
        assert s.introspect_service_api_key == _INTROSPECT_KEY

    @pytest.mark.parametrize("app_env", ["local", "development", "test"])
    def test_collision_not_enforced_outside_production(self, monkeypatch, app_env):
        """Вне production гард не активируется — dev-стенды могут шарить ключ."""
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": app_env,
                "SERVICE_API_KEYS": _PER_SERVICE_KEYS,
                "INTROSPECT_SERVICE_API_KEY": "k-auth-1234567890",
            },
        )
        assert s.introspect_service_api_key == "k-auth-1234567890"


class TestLegacySharedKeyFieldRemoved:
    """Регрессионный гард: поле `service_api_key` больше нет в Settings,
    `SERVICE_API_KEY` env vars никак не влияет на конфиг.
    """

    def test_settings_has_no_service_api_key_attr(self, monkeypatch):
        s = _load_settings_fresh(monkeypatch, {})
        assert not hasattr(s, "service_api_key")

    def test_legacy_env_var_ignored(self, monkeypatch):
        """`SERVICE_API_KEY=...` в env не должен влиять на загрузку Settings,
        в т.ч. — не падать с extra-field ошибкой (`Settings.extra="ignore"`).
        """
        s = _load_settings_fresh(
            monkeypatch,
            {
                "APP_ENV": "local",
                "SERVICE_API_KEY": "ignored-legacy-value",
            },
        )
        # Loaded without error, legacy field absent.
        assert not hasattr(s, "service_api_key")
