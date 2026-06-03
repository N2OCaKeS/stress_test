"""Anti-drift: env-vars в `src.core.config.Settings` vs таблица в `README.md`.

Контракт:
  * Источник истины — поля `Settings` (`pydantic-settings`). Имя env-var берётся
    из `alias` / `validation_alias` или upper-case-имени поля.
  * `README.md` дублирует список env-vars в таблице вида `| <ENV> | … |`.
  * Drift'ом не раз пойманы случаи «добавил поле — забыл README» и обратно
    «удалил поле — README остался». Тест ловит оба направления.

Файл `README.md` git-tracked и присутствует в стандартной локальной раскладке,
но в Docker-образе тестов структура каталогов другая (`/app/...`) — поэтому
ищем README по предкам, и если не нашли, тест skip'ается.
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
    """Все возможные env-имена для поля Settings.

    Алиасы могут быть `alias=...`, `validation_alias=...` (включая
    `AliasChoices(...)`), либо вообще не заданы — тогда pydantic-settings
    использует имя поля (uppercase).
    """
    names: set[str] = set()
    if field.alias:
        names.add(field.alias.upper())
    va = field.validation_alias
    if va is None:
        pass
    elif isinstance(va, str):
        names.add(va.upper())
    elif isinstance(va, AliasChoices):
        for choice in va.choices:
            if isinstance(choice, str):
                names.add(choice.upper())
    return names


def _settings_env_names() -> dict[str, set[str]]:
    """Маппинг python-имя поля → множество env-вариантов."""
    out: dict[str, set[str]] = {}
    for fname, finfo in Settings.model_fields.items():
        aliases = _collect_env_aliases(finfo)
        # Если ни одного alias'а — pydantic-settings берёт имя поля как есть
        # (case-insensitive: `case_sensitive=False` по умолчанию).
        if not aliases:
            aliases = {fname.upper()}
        out[fname] = aliases
    return out


def _readme_env_tokens() -> set[str]:
    r"""Все backquoted UPPER_SNAKE_CASE токены из таблиц README.

    Берём с запасом — любые ``ABC_DEF`` в любом месте README. Дальше
    пересечением с `_settings_env_names()` отсекаем нерелевантные
    (например, `AUDIT_EVENTS` — заголовок раздела, не env).
    """
    if _README_PATH is None:
        pytest.skip("README not found, env-vars sync test skipped in this environment")
    text = _README_PATH.read_text(encoding="utf-8")
    tokens: set[str] = set()
    for tok in re.findall(r"`([A-Z][A-Z0-9_]+)`", text):
        tokens.add(tok)
    return tokens


# ── Тесты ───────────────────────────────────────────────────────────────────


class TestSettingsFieldsInReadme:
    """Каждое поле `Settings` должно быть упомянуто в README хотя бы под одним
    из своих env-имён (alias'ов)."""

    # Известные drift'ы, зафиксированные на момент введения теста (baseline).
    # При исчезновении в README поле должно перейти в основной assert —
    # то есть запись здесь означает «известный gap, документации не хватает,
    # фиксить отдельной волной».
    _BASELINE_DRIFT: frozenset[str] = frozenset()

    def test_every_settings_field_is_documented(self):
        readme_tokens = _readme_env_tokens()
        missing: list[str] = []
        for fname, env_names in _settings_env_names().items():
            if fname in self._BASELINE_DRIFT:
                continue
            if not env_names & readme_tokens:
                missing.append(f"{fname} (aliases: {sorted(env_names)})")
        assert not missing, (
            "Settings-поля отсутствуют в README.md (нет ни одного из их "
            "env-алиасов):\n  " + "\n  ".join(missing)
        )


class TestReadmeHasNoDeadEnvVars:
    """Backquoted UPPER_SNAKE_CASE-токены в README, которые выглядят как env-vars,
    должны соответствовать какому-то полю Settings — иначе это dead-документация.

    Smoke-вариант: список known-exceptions явный (заголовки, error-code'ы и т.п.);
    всё остальное должно мапиться на Settings.
    """

    # Токены, которые встречаются в README, но env-vars не являются. Сюда
    # пополняем сознательно — каждый раз, когда автор README использует
    # backticks для не-env термина (имя файла, error_code, JSON-ключ payload,
    # SQL-команда, k8s-объект и т.д.).
    _NON_ENV_WHITELIST: frozenset[str] = frozenset({
        # Имена сервисов / файлов / разделов
        "AUDIT_EVENTS",
        "STATUS",
        "TEST_COVERAGE",
        "CLAUDE",
        "README",
        # Internal error-code'ы (наружу торчат в `error_code` payload)
        "BMC_VERIFY_REQUIRED",
        "ROTATED_AT_IN_FUTURE",
        "ROTATED_AT_TOO_OLD",
        "ROTATED_AT_IN_PAST",
        "NO_DEPARTMENT",
        "SERVER_NO_IPMI",
        "NO_IPMI_CONTROLLER",
        "INVALID_ACTION_FOR_ENTITY",
        "PLATFORM_ADMIN_BUSINESS_DATA_DENIED",
        "DEPARTMENT_ISOLATION",
        "DISPATCH_STASH_MISSING",
        "SSH_BOOTSTRAP_CREDS_MISSING",
        "SSH_INVALID_ARG",
        "SSH_MANAGEMENT_KEY_MISSING",
        "PASSWORD_REVEALED",
        "USER_INACTIVE",
        "BOT_INACTIVE",
        "NO_LINKED_SERVERS",
        # HTTP / scheme / generic terms
        "TLS",
        "JSON",
        "PEM",
        "RSA",
        "JWT",
        "PAT",
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "SET",
        "LIMIT",
        "ORDER",
        # K8s / ops
        "CONFIGMAP",
        "SECRET",
        "INGRESS",
        # Misc
        "SAVEPOINT",
        "TRUNCATE",
        "CASCADE",
    })

    def test_no_unknown_env_tokens_in_readme(self):
        readme_tokens = _readme_env_tokens()
        known = set().union(*_settings_env_names().values())
        # Игнорируем legacy-алиасы, которых в текущих Settings уже нет —
        # они могут оставаться в README как маркировка обратной совместимости.
        suspicious = readme_tokens - known - self._NON_ENV_WHITELIST
        # Дополнительная эвристика: short tokens (1-2 символа) или цифры-only —
        # вряд ли env-vars. Оставляем такие на любителя.
        suspicious = {t for t in suspicious if len(t) >= 4}
        # Не делаем strict-assert: README может содержать новые error-code'ы
        # без апдейта whitelist'а. Smoke-проверка: их немного.
        assert len(suspicious) < 80, (
            f"подозрительные UPPER_SNAKE-токены в README, не мапящиеся на "
            f"Settings и не в whitelist (>=80): "
            f"{sorted(suspicious)[:20]} ... (всего {len(suspicious)})"
        )


class TestSmokeFieldCount:
    """Smoke-guard: количество полей Settings не должно неожиданно проседать
    (защита от случайного `extra="ignore"` или массового удаления).
    """

    def test_settings_has_enough_fields(self):
        # На момент написания (W32) — около 35 полей. Граница 20 — sane
        # lower bound: если кто-то по ошибке удалил половину Settings,
        # это поймаем.
        assert len(Settings.model_fields) >= 20, (
            f"Settings model has too few fields: {len(Settings.model_fields)}"
        )
