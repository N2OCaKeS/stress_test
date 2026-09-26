"""Pydantic-схемы для эндпоинтов /test-definitions.

Каталог тестов — per-department бизнес-данные (§2.2 плана миграции), в
отличие от платформенных `global_variables`. Чтение доступно любому
аутентифицированному актору, запись — под матрицей прав
`(test_definition, *, create|update|delete)`.
"""

import re
from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.core.constants import DatesQuoting, TestMode, TestReadiness, VerdictSource

# Код теста: латиница/цифры, `_`/`-`/`.` как разделители, не начинается с
# разделителя. Формат достаточно свободный, чтобы вместить легаси-имена вида
# `postgresql.balance` или `kernel_fill`, но защищает от пустых/пробельных строк.
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def _validate_code(value: str) -> str:
    stripped = value.strip()
    if not _CODE_RE.match(stripped):
        raise ValueError(
            "code must start with an alphanumeric and contain only letters, "
            "digits, '_', '-' or '.'"
        )
    return stripped


# Параметр ядра уходит в /etc/default/grub — тот же allow-list, что у
# server_worker (`_stand_setup_helpers._CMDLINE_PARAM_RE`).
_CMDLINE_RE = re.compile(r"^[A-Za-z0-9._,:=/+-]{1,128}$")


class StandSetup(BaseModel):
    """Шаг настройки стенда теста.

    Выполняется при подготовке стенда (после заведения учётки, sudo NOPASSWD
    уже есть), затем стенд перезагружается.
    """

    kernel_cmdline_extra: list[str] = Field(
        default_factory=list, max_length=32,
        description="Доп. параметры ядра в GRUB_CMDLINE_LINUX_DEFAULT (например, audit=0).",
    )
    script: str = Field(
        default="", max_length=200_000,
        description="Bash-скрипт, подстановки {{CODE}} из каталога переменных; sensitive маскируются в логах.",
    )
    run_as: Literal["root", "test_user"] = Field(default="root", description="От root или от тестовой учётки.")
    phase: Literal["before_kernel", "after_boot"] = Field(
        default="after_boot",
        description="after_boot — после подготовки стенда и перезагрузки (по умолчанию); before_kernel — до смены ядра.",
    )
    reboot_after: bool | None = Field(
        default=None, description="Перезагрузить после скрипта; пусто — да, если задан скрипт или параметры ядра.",
    )
    timeout_seconds: int = Field(default=1800, ge=10, le=86400)

    @field_validator("kernel_cmdline_extra")
    @classmethod
    def _check_params(cls, value: list[str]) -> list[str]:
        cleaned = [p.strip() for p in value if p.strip()]
        for param in cleaned:
            if not _CMDLINE_RE.match(param):
                raise ValueError(f"kernel parameter {param!r}: only letters, digits and ._,:=/+- are allowed")
        return cleaned


class TestDefinitionCreate(BaseModel):
    """Тело POST /test-definitions. `code` уникален."""

    code: str = Field(
        ..., min_length=1, max_length=64,
        description="Машинный код теста (например, postgresql.balance). UNIQUE.",
    )
    full_name: str = Field(
        ..., min_length=1, max_length=256,
        description="Человекочитаемое название теста.",
    )
    category: str | None = Field(default=None, max_length=64, description="Категория теста.")
    matrix_label: str | None = Field(
        default=None, max_length=64,
        description=(
            "Короткая подпись строки в СТП-матрице (\"FS_EXT4\"). Пусто — "
            "матрица печатает полное название."
        ),
    )
    short_name: str | None = Field(
        default=None, max_length=64,
        description=(
            "Короткое имя теста (\"XFS\", \"postgresql-sm\") — легаси-ключ словаря "
            "tests; его подставляет переменная TEST_SHORT_NAME. Пусто — полное название."
        ),
    )
    dates_quoting: DatesQuoting = Field(
        default=DatesQuoting.SHELL,
        description=(
            "Экранирование токенов в dates.conf: shell — shlex.quote каждого токена; "
            "legacy — двойные кавычки у токенов с пробелом (как backup_image.py); "
            "raw — без экранирования."
        ),
    )
    launch_profile_id: str | None = Field(
        default=None, max_length=64,
        description="Профиль запуска; пусто — профиль отдела по умолчанию.",
    )
    provisioning_profile_id: str | None = Field(
        default=None, max_length=64,
        description="Профиль подготовки стенда; пусто — профиль отдела по умолчанию.",
    )
    stand_setup: StandSetup | None = Field(
        default=None, description="Шаг настройки стенда первого шага теста.",
    )
    verdict_source: VerdictSource = Field(
        default=VerdictSource.ZEPHYR,
        description=(
            "Откуда брать исход теста: zephyr — статус, который скрипт выставил "
            "тест-кейсу в прогоне Zephyr; exit_code — код выхода starter.sh."
        ),
    )
    owner: str | None = Field(default=None, max_length=128, description="Ответственный за тест.")
    readiness: TestReadiness = Field(
        default=TestReadiness.DEVELOPMENT,
        description="ready — Рабочий; review — На проверке; broken — Неисправен; development — В разработке. Только ready допускает обычный запуск.",
    )
    mode: TestMode = Field(
        default=TestMode.OREL,
        description=(
            "Режим безопасности Astra, под которым тест исполняется — фиксируется "
            "здесь, не выбирается при запуске/в кампании. server_worker переключает "
            "на него стенд перед прогоном."
        ),
    )
    department_id: str | None = Field(
        default=None, description="Отдел-владелец теста. Пусто — платформенный тест.",
    )
    pinned_stand_id: str | None = Field(
        default=None,
        description=(
            "Стенд, к которому привязан тест. Ссылка без FK — стенды "
            "появятся отдельным доменом; снимается debug-режимом при запуске."
        ),
    )
    changelog_component: str | None = Field(
        default=None, max_length=128,
        description=(
            "Компонент ОС, используемый ТОЛЬКО changelog-фильтром СТП-генерации "
            "(§7). Пусто — тест в changelog-объём не попадает (как в легаси); "
            "в полный набор попадает по-прежнему."
        ),
    )
    starter_suffix: str | None = Field(
        default=None, max_length=16,
        description=(
            "Позиционный $5 у legacy starter.sh (kernel/balance/oom) первого шага "
            "теста. Пусто — generic `run.py -n <dates_filename>` без "
            "дополнительного флага."
        ),
    )
    timeout_seconds: int | None = Field(
        default=None, gt=0,
        description="Свой SSH-таймаут исполнения (сек). Пусто — дефолт testing_worker'а.",
    )
    priority: int = Field(
        default=0, ge=-1000, le=1000,
        description=(
            "Приоритет теста для ключа `priority` правила сортировки прогона РЦ "
            "(настройки тестирования отдела). По умолчанию 0."
        ),
    )

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        return _validate_code(value)


class TestDefinitionUpdate(BaseModel):
    """Тело PATCH /test-definitions/{test_id}. Все поля опциональны."""

    code: str | None = Field(default=None, min_length=1, max_length=64, description="Сменить код (UNIQUE).")
    full_name: str | None = Field(default=None, min_length=1, max_length=256, description="Сменить название.")
    category: str | None = Field(default=None, max_length=64, description="Сменить категорию.")
    matrix_label: str | None = Field(
        default=None, max_length=64, description="Сменить короткую подпись строки в СТП-матрице.",
    )
    short_name: str | None = Field(
        default=None, max_length=64, description="Сменить короткое имя теста (TEST_SHORT_NAME).",
    )
    dates_quoting: DatesQuoting | None = Field(
        default=None, description="Сменить режим экранирования dates.conf (shell/legacy/raw).",
    )

    @field_validator("dates_quoting")
    @classmethod
    def _check_dates_quoting(cls, value: DatesQuoting | None) -> DatesQuoting:
        if value is None:
            raise ValueError("dates_quoting cannot be null")
        return value

    verdict_source: VerdictSource | None = Field(
        default=None, description="Сменить источник исхода теста (zephyr/exit_code).",
    )
    launch_profile_id: str | None = Field(
        default=None, max_length=64, description="Сменить профиль запуска; null — профиль отдела по умолчанию.",
    )
    provisioning_profile_id: str | None = Field(
        default=None, max_length=64, description="Сменить профиль подготовки; null — профиль отдела.",
    )
    stand_setup: StandSetup | None = Field(
        default=None, description="Заменить шаг настройки стенда первого шага теста; null — убрать.",
    )

    @field_validator("verdict_source")
    @classmethod
    def _check_verdict_source(cls, value: VerdictSource | None) -> VerdictSource:
        if value is None:
            raise ValueError("verdict_source cannot be null")
        return value
    owner: str | None = Field(default=None, max_length=128, description="Сменить ответственного.")
    readiness: TestReadiness | None = Field(default=None, description="Сменить статус теста вручную.")

    @field_validator("readiness")
    @classmethod
    def _check_readiness(cls, value: TestReadiness | None) -> TestReadiness:
        if value is None:
            raise ValueError("readiness cannot be null")
        return value
    mode: TestMode | None = Field(default=None, description="Сменить режим безопасности теста.")

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: TestMode | None) -> TestMode:
        if value is None:
            raise ValueError("mode cannot be null")
        return value
    department_id: str | None = Field(default=None, description="Сменить отдел-владелец.")
    pinned_stand_id: str | None = Field(default=None, description="Сменить привязанный стенд.")
    changelog_component: str | None = Field(
        default=None, max_length=128, description="Сменить компонент changelog-фильтра.",
    )
    starter_suffix: str | None = Field(
        default=None, max_length=16, description="Сменить позиционный $5 у legacy starter.sh первого шага.",
    )
    timeout_seconds: int | None = Field(
        default=None, gt=0, description="Сменить свой SSH-таймаут исполнения (сек).",
    )
    priority: int | None = Field(
        default=None, ge=-1000, le=1000,
        description="Сменить приоритет теста для правила сортировки прогона РЦ.",
    )

    @field_validator("priority")
    @classmethod
    def _check_priority(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("priority cannot be null")
        return value

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_code(value)


class TestDefinitionResponse(BaseModel):
    """Карточка теста в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Test definition ID (prefix tdef_).")
    code: str = Field(description="Машинный код теста.")
    full_name: str = Field(description="Отображаемое название.")
    category: str | None = Field(default=None, description="Категория теста.")
    matrix_label: str | None = Field(default=None, description="Короткая подпись строки в СТП-матрице.")
    short_name: str | None = Field(default=None, description="Короткое имя теста (TEST_SHORT_NAME).")
    dates_quoting: DatesQuoting = Field(description="Экранирование токенов в dates.conf: shell/legacy/raw.")
    verdict_source: VerdictSource = Field(description="Источник исхода теста: zephyr/exit_code.")
    launch_profile_id: str | None = Field(default=None, description="Профиль запуска; пусто — профиль отдела.")
    provisioning_profile_id: str | None = Field(default=None, description="Профиль подготовки; пусто — профиль отдела.")
    stand_setup: StandSetup | None = Field(
        default=None, description="Шаг настройки стенда первого шага теста.",
    )
    owner: str | None = Field(default=None, description="Ответственный за тест.")
    readiness: TestReadiness = Field(description="Ручной статус теста; не зависит от исхода запуска.")
    mode: TestMode = Field(description="Режим безопасности Astra, под которым тест исполняется.")
    department_id: str | None = Field(default=None, description="Отдел-владелец.")
    pinned_stand_id: str | None = Field(default=None, description="Привязанный стенд.")
    changelog_component: str | None = Field(default=None, description="Компонент changelog-фильтра СТП.")
    starter_suffix: str | None = Field(default=None, description="Позиционный $5 у legacy starter.sh первого шага.")
    timeout_seconds: int | None = Field(default=None, description="Свой SSH-таймаут исполнения (сек); пусто — дефолт testing_worker'а.")
    priority: int = Field(default=0, description="Приоритет теста для правила сортировки прогона РЦ.")
    created_at: datetime = Field(description="Когда тест заведён.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
    created_by: str | None = Field(default=None, description="Кто завёл.")
