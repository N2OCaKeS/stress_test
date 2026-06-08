"""Юнит-тесты: production-guard для `DOCKER_RSA_PRIVATE_KEY` и ephemeral-fallback warning.

Покрывает ephemeral RSA-key fallback без production-guard для docker_jwt:

* `Settings` падает с `ValidationError`, если `APP_ENV=production` и
  `DOCKER_RSA_PRIVATE_KEY` не задан — это не даёт сервису молча стартовать
  с эфемерным ключом, который перестаёт совпадать с
  Docker registry `rootcertbundle` после первого рестарта.
* Non-production режим (`local`/`development`/`test`) спокойно работает без
  ключа — в логе только WARNING.
* Production + валидный PEM проходит без ошибок.
"""

import logging

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError


# ── Фикстуры/хелперы ─────────────────────────────────────────────────────────


def _generate_rsa_pem() -> str:
    """Сгенерировать валидный PEM-приватник для positive-кейсов."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem_bytes.decode()


def _prod_kwargs(**overrides) -> dict:
    """Базовый набор параметров для production-сборки `Settings`.

    Прочие production-валидаторы (`SECRET_KEY`/`SERVICE_API_KEY` length+
    placeholder-check, `APP_DEBUG`, `LOGGING_SERVICE_API_KEY`) закрыты здесь
    рабочими значениями — мы тестируем именно `DOCKER_RSA_PRIVATE_KEY`-guard,
    а не остальные prod-проверки.
    """
    base = {
        "APP_ENV": "production",
        "APP_DEBUG": False,
        # ≥ 32 chars, без `change-me`/`changeme` substring (см. _WEAK_SECRET_SUBSTRINGS).
        "SECRET_KEY": "prod-secret-key-strong-random-value-1234567890",
        "SERVICE_API_KEY": "prod-service-api-key-strong-random-value-1234567890",
        # Без него `_validate_production_secrets` падает раньше, чем дойдёт
        # до docker-проверки (см. `core/config.py:_validate_production_secrets`).
        "LOGGING_SERVICE_API_KEY": "prod-logging-api-key-strong-random-value-1234567890",
        # Lifecycle-guard в production-validator'е требует непустой ключ;
        # URL может быть пустым (no-op).
        "SECRET_INTERNAL_API_KEY": "prod-secret-internal-api-key-1234567890",
    }
    base.update(overrides)
    return base


# ── Production-guard ─────────────────────────────────────────────────────────


class TestProductionDockerKeyGuard:
    """`Settings._validate_production_secrets` для `DOCKER_RSA_PRIVATE_KEY`."""

    def test_production_without_docker_key_raises_validation_error(self):
        """APP_ENV=production + пустой DOCKER_RSA_PRIVATE_KEY → ValidationError."""
        from src.core.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(**_prod_kwargs(DOCKER_RSA_PRIVATE_KEY=""))

        # Сообщение валидатора должно явно указывать на причину.
        assert "DOCKER_RSA_PRIVATE_KEY" in str(exc_info.value)

    def test_production_with_unset_docker_key_raises_validation_error(self):
        """APP_ENV=production + ключ вовсе не задан (None) → ValidationError.

        `Field(default=None)` означает, что без env-переменной поле = None,
        что эквивалентно пустой строке для production-guard.
        """
        from src.core.config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(**_prod_kwargs())

        assert "DOCKER_RSA_PRIVATE_KEY" in str(exc_info.value)

    def test_production_with_valid_pem_is_ok(self):
        """APP_ENV=production + валидный PEM → Settings создаётся без ошибок."""
        from src.core.config import Settings

        pem = _generate_rsa_pem()
        s = Settings(**_prod_kwargs(DOCKER_RSA_PRIVATE_KEY=pem))
        assert s.app_env == "production"
        assert s.docker_rsa_private_key == pem


# ── Non-production: ephemeral fallback ───────────────────────────────────────


class TestNonProductionEphemeralFallback:
    """`Settings` без ключа проходит вне production; `docker_jwt` логирует WARNING."""

    @pytest.mark.parametrize("env", ["local", "development", "test"])
    def test_non_production_without_docker_key_is_ok(self, env):
        """Без production-флага guard молчит — это dev-режим."""
        from src.core.config import Settings

        s = Settings(APP_ENV=env, DOCKER_RSA_PRIVATE_KEY=None)
        assert s.app_env == env
        assert s.docker_rsa_private_key is None

    def test_ephemeral_key_generation_emits_warning(self, monkeypatch, caplog):
        """`_get_rsa_private_key` без настроенного ключа → WARNING в лог.

        Цель: операторы видят в логах, что Docker registry tokens перестанут
        валидироваться после рестарта auth_service. Сам ключ всё равно
        генерируется, чтобы сервис мог стартовать в dev.
        """
        # Подменяем настройки на dev-режим с пустым ключом и сбрасываем
        # lru_cache, чтобы наш вызов был «первым» (иначе warning уже мог
        # быть напечатан в другом тесте и не попадёт в caplog).
        from src.core import docker_jwt
        from src.core.config import Settings

        fake_settings = Settings(APP_ENV="local", DOCKER_RSA_PRIVATE_KEY=None)
        monkeypatch.setattr(docker_jwt, "get_settings", lambda: fake_settings)
        docker_jwt._get_rsa_private_key.cache_clear()

        with caplog.at_level(logging.WARNING, logger=docker_jwt.logger.name):
            key = docker_jwt._get_rsa_private_key()

        # Сбрасываем кэш обратно, чтобы не повлиять на следующие тесты.
        docker_jwt._get_rsa_private_key.cache_clear()

        assert key is not None  # ключ всё-таки выдан — сервис должен стартовать в dev
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "ephemeral fallback must emit at least one WARNING record"
        message = " ".join(r.getMessage() for r in warnings)
        assert "ephemeral" in message.lower()
        assert "DOCKER_RSA_PRIVATE_KEY" in message

    def test_configured_key_does_not_emit_ephemeral_warning(self, monkeypatch, caplog):
        """С настроенным `DOCKER_RSA_PRIVATE_KEY` WARNING не должен появляться."""
        from src.core import docker_jwt
        from src.core.config import Settings

        pem = _generate_rsa_pem()
        fake_settings = Settings(APP_ENV="local", DOCKER_RSA_PRIVATE_KEY=pem)
        monkeypatch.setattr(docker_jwt, "get_settings", lambda: fake_settings)
        docker_jwt._get_rsa_private_key.cache_clear()

        with caplog.at_level(logging.WARNING, logger=docker_jwt.logger.name):
            key = docker_jwt._get_rsa_private_key()

        docker_jwt._get_rsa_private_key.cache_clear()

        assert key is not None
        ephemeral_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "ephemeral" in r.getMessage().lower()
        ]
        assert not ephemeral_warnings, (
            "configured key path must not log ephemeral-fallback warning"
        )
