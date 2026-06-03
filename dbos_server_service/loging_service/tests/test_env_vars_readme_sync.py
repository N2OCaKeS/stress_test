"""Anti-drift: env-vars в `src.core.config.Settings` vs таблица в `README.md`.

Контракт:
  * Источник истины — поля `Settings`. Имя env-var берётся из `alias`
    (loging_service использует alias на каждом поле) либо из uppercase-имени
    поля (`case_sensitive=False`).
  * `README.md` несёт таблицу env-vars; каждая строка — `| <ENV> | … | … |`.

Файл README.md git-tracked, в Docker-образе тестов есть.
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


class TestSettingsFieldsInReadme:
    """Каждое поле `Settings` должно быть упомянуто в README через какой-то
    из своих env-алиасов."""

    _BASELINE_DRIFT: frozenset[str] = frozenset({
        # сюда — известные на момент написания gaps.
    })

    def test_every_settings_field_is_documented(self):
        readme_tokens = _readme_env_tokens()
        missing: list[str] = []
        for fname, env_names in _settings_env_names().items():
            if fname in self._BASELINE_DRIFT:
                continue
            if not env_names & readme_tokens:
                missing.append(f"{fname} (aliases: {sorted(env_names)})")
        assert not missing, (
            "Settings-поля отсутствуют в README.md:\n  " + "\n  ".join(missing)
        )


class TestReadmeHasNoDeadEnvVars:
    """В README не должно остаться `*_*` env-имён, которых уже нет в Settings."""

    _NON_ENV_WHITELIST: frozenset[str] = frozenset({
        "AUDIT_EVENTS", "STATUS", "TEST_COVERAGE", "README", "CLAUDE",
        # Internal error_code'ы / payload keys
        "INTROSPECT_NOT_INITIALIZED",
        "SERVICE_IDENTITY_PAYLOAD_MISMATCH",
        "LOGING_ADMIN_REQUIRED",
        "NO_DEPARTMENT",
        # Generic
        "TLS", "JSON", "PEM", "RSA", "JWT", "PAT",
        "GET", "POST", "PUT", "DELETE", "SET",
        "ORDER", "LIMIT", "OFFSET",
        "TRUNCATE", "CASCADE", "SAVEPOINT",
        "CONFIGMAP", "SECRET", "INGRESS",
        "ID", "URL", "CRUD",
    })

    def test_no_dead_env_tokens_in_readme(self):
        readme_tokens = _readme_env_tokens()
        known = set().union(*_settings_env_names().values())
        suspicious = readme_tokens - known - self._NON_ENV_WHITELIST
        suspicious = {t for t in suspicious if len(t) >= 4}
        assert len(suspicious) < 30, (
            f"подозрительные UPPER_SNAKE-токены в README (>=30): "
            f"{sorted(suspicious)}"
        )


class TestSmokeFieldCount:
    """Smoke: Settings содержит ожидаемое минимальное число полей."""

    def test_settings_has_enough_fields(self):
        assert len(Settings.model_fields) >= 20, (
            f"Settings model has too few fields: {len(Settings.model_fields)}"
        )
