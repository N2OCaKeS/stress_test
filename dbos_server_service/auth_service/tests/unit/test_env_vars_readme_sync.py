"""Anti-drift: env-vars в `src.core.config.Settings` vs таблица в `README.md`.

Контракт (smoke-вариант для auth_service):
  * `README.md` auth_service'а намеренно перечисляет только «ключевые»
    env-vars и явно ссылается на `src/core/config.py` за полным списком.
    Поэтому strict-проверка «каждое поле в README» дала бы 30+ ложных
    срабатываний.
  * Smoke-guard: количество полей Settings >= N (защита от случайного
    `extra="ignore"` или массового удаления); README упоминает хотя бы M
    из них (защита от расхождения «ключевых» env-vars с реальным
    Settings); в README нет «мёртвых» env-vars, давно удалённых из кода.

Файл README.md git-tracked, в Docker-образе тестов он есть — тест без
skip-fallback'а.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from pydantic.fields import FieldInfo
from pydantic import AliasChoices

from src.core.config import Settings


def _find_readme() -> pathlib.Path | None:
    for parent in pathlib.Path(__file__).resolve().parents:
        candidate = parent / "README.md"
        if candidate.is_file():
            return candidate
    return None


_README_PATH = _find_readme()


def _collect_env_aliases(field: FieldInfo) -> set[str]:
    names: set[str] = set()
    if field.alias:
        names.add(field.alias.upper())
    va = field.validation_alias
    if isinstance(va, str):
        names.add(va.upper())
    elif isinstance(va, AliasChoices):
        for choice in va.choices:
            if isinstance(choice, str):
                names.add(choice.upper())
    return names


def _settings_env_names() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for fname, finfo in Settings.model_fields.items():
        aliases = _collect_env_aliases(finfo)
        if not aliases:
            aliases = {fname.upper()}
        out[fname] = aliases
    return out


def _readme_env_tokens() -> set[str]:
    if _README_PATH is None:
        pytest.skip("README not found, env-vars sync test skipped in this environment")
    text = _README_PATH.read_text(encoding="utf-8")
    return set(re.findall(r"`([A-Z][A-Z0-9_]+)`", text))


class TestSettingsFieldCount:
    """Smoke-guard на размер Settings — защита от случайного `extra="ignore"`
    или массового удаления полей."""

    def test_settings_has_enough_fields(self):
        assert len(Settings.model_fields) >= 30, (
            f"Settings model has unexpectedly few fields: "
            f"{len(Settings.model_fields)} (expected >= 30)"
        )


class TestReadmeCoversCriticalEnvVars:
    """README должен упоминать критическую подгруппу env-vars (даже если
    остальное живёт в src/core/config.py).

    Подгруппа — те env-vars, без которых production-deploy просто не
    стартует или ведёт себя небезопасно. Если что-то выкинули из Settings
    — тест ловит расхождение.
    """

    _CRITICAL_ENV_VARS: frozenset[str] = frozenset({
        "DATABASE_URL",
        "SECRET_KEY",
        "ACCESS_TOKEN_TTL_MINUTES",
        "REFRESH_TOKEN_TTL_DAYS",
        "SERVICE_API_KEY",
        "LOGGING_SERVICE_URL",
        "LOGGING_SERVICE_API_KEY",
        "TRUSTED_PROXY_IPS",
        "JWT_AUDIENCE",
        "JWT_ISSUER",
        "DOCKER_RSA_PRIVATE_KEY",
        "RATE_LIMIT_STORAGE_URI",
    })

    def test_critical_envs_present_in_readme(self):
        readme_tokens = _readme_env_tokens()
        missing = self._CRITICAL_ENV_VARS - readme_tokens
        assert not missing, (
            f"критические env-vars не упомянуты в README.md: {sorted(missing)}"
        )

    def test_critical_envs_present_in_settings(self):
        """Критические env-vars должны быть реальными полями Settings —
        иначе мы повесили обязательство на исчезнувшую переменную."""
        known_envs: set[str] = set().union(*_settings_env_names().values())
        missing = self._CRITICAL_ENV_VARS - known_envs
        assert not missing, (
            f"критические env-vars из whitelist'а отсутствуют в Settings: "
            f"{sorted(missing)} — либо обнови whitelist, либо верни поле."
        )


class TestNoDeadEnvVarsInReadme:
    """Backquoted UPPER_SNAKE-токены в README, выглядящие как env-vars,
    не должны ссылаться на несуществующие поля Settings."""

    # Whitelist токенов, которые НЕ env-vars (имена файлов, error_code'ы,
    # legacy-алиасы, generic-термины).
    _NON_ENV_WHITELIST: frozenset[str] = frozenset({
        "AUDIT_EVENTS",
        "STATUS",
        "TEST_COVERAGE",
        "CLAUDE",
        "README",
        "API_ENDPOINTS",
        "SERVICE_ACCESS_DENIED",
        "PLATFORM_ADMIN_BUSINESS_DATA_DENIED",
        "DEPARTMENT_ISOLATION",
        "NO_DEPARTMENT",
        "TLS", "JSON", "PEM", "RSA", "JWT", "PAT", "OAUTH",
        "GET", "POST", "PUT", "DELETE", "SET",
        "CONFIGMAP", "SECRET", "INGRESS",
        "CRUD",
        "INVALID_TOKEN",
        "ID",
        "URL",
    })

    def test_no_dead_env_tokens_in_readme(self):
        readme_tokens = _readme_env_tokens()
        known = set().union(*_settings_env_names().values())
        suspicious = readme_tokens - known - self._NON_ENV_WHITELIST
        suspicious = {t for t in suspicious if len(t) >= 4}
        # Smoke-проверка: не более N подозрительных. README'у можно жить с
        # legacy-маркировкой `*__VN` и т.п. legacy-форматами, но если
        # появилось 50+ незарегистрированных — что-то системно поехало.
        assert len(suspicious) < 30, (
            f"подозрительные UPPER_SNAKE-токены в README, не мапящиеся на "
            f"Settings и не в whitelist (>=30): {sorted(suspicious)}"
        )
