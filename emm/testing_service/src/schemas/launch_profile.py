"""Pydantic-схемы `/launch-profiles`."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_PATH_KEYS = ("script", "dates", "token", "testenv_marker", "command_file")


class LaunchProfileClone(BaseModel):
    """Как `starter.sh` клонирует ветку теста."""

    repo_url: str = Field(..., min_length=1, max_length=512, description="URL репозитория с тестами.")
    mode: Literal["branch", "full"] = Field(
        default="branch",
        description="branch — только ветка теста (--single-branch, как легаси); full — все ветки.",
    )
    depth: int | None = Field(default=None, ge=1, le=100000, description="--depth; пусто — вся история (легаси).")
    credential: Literal["git"] = Field(
        default="git",
        description="Токен git — credential интеграции отдела (git_credential_id, фолбэк bitbucket_credential_id).",
    )

    @field_validator("repo_url")
    @classmethod
    def _no_spaces(cls, value: str) -> str:
        value = value.strip()
        if any(ch.isspace() for ch in value) or "'" in value or '"' in value:
            raise ValueError("repo_url must not contain spaces or quotes")
        return value


class LaunchProfilePaths(BaseModel):
    """Шаблоны путей на стенде (`{CODE}` — переменные каталога, `{QUEUE_ITEM_ID}`)."""

    script: str = Field(default="{TEST_HOME}/starter.sh", max_length=256)
    dates: str = Field(default="{TEST_HOME}/dates_{QUEUE_ITEM_ID}.conf", max_length=256)
    token: str = Field(default="{TEST_HOME}/git_token_{QUEUE_ITEM_ID}.conf", max_length=256)
    testenv_marker: str = Field(default="{TEST_HOME}/testenv_marker.conf", max_length=256)
    command_file: str = Field(default="{TEST_HOME}/command.txt", max_length=256)

    @field_validator(*_PATH_KEYS)
    @classmethod
    def _check(cls, value: str) -> str:
        value = value.strip()
        if not value or not re.fullmatch(r"[A-Za-z0-9_./{}\-]+", value):
            raise ValueError("path template may contain only letters, digits, _ . / - and {CODE}")
        return value


class LaunchProfileTestenv(BaseModel):
    """testenv-маркер (T2)."""

    on_value: str = Field(default="on", min_length=1, max_length=32)
    off_value: str = Field(default="off", min_length=1, max_length=32)
    cleanup_other: bool = Field(
        default=False,
        description="Удалять прочие testenv_*.conf рядом с маркером перед записью (по умолчанию нет: сервер чистый).",
    )


class LaunchProfileExtraFile(BaseModel):
    """Дополнительный файл, который профиль кладёт на стенд.

    Путь — шаблон `{CODE}`, как пути профиля; содержимое — `{{CODE}}`, как
    `starter.sh`. Файл с подставленной sensitive-переменной (или с
    `sensitive=true`) не пишется в лог открытым текстом.
    """

    path: str = Field(..., min_length=1, max_length=256)
    content: str = Field(default="", max_length=65_536)
    mode: str = Field(default="0644", pattern=r"^0[0-7]{3}$")
    sensitive: bool = False

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return LaunchProfilePaths._check(value)


class LaunchProfileVersionInput(BaseModel):
    """Содержимое новой версии профиля."""

    comment: str | None = Field(default=None, max_length=512)
    starter_script: str = Field(..., min_length=1, max_length=200_000, description="Текст starter.sh, подстановки {{CODE}}.")
    clone: LaunchProfileClone
    paths: LaunchProfilePaths = Field(default_factory=LaunchProfilePaths)
    launch_command_template: str = Field(
        ..., min_length=1, max_length=2048,
        description="Команда запуска: токены по пробелам, {CODE} в каждом; каждый токен экранируется отдельно.",
    )
    stop_command_template: str = Field(
        ..., min_length=1, max_length=8192,
        description="Команда остановки (shell), подстановки {{CODE}}: {{STARTER_PGREP_PATTERN}}, {{STOP_GRACE_SECONDS}}.",
    )
    stop_grace_seconds: int = Field(default=10, ge=0, le=600)
    use_pty: bool = Field(default=True, description="Исполнять в терминале (pty): вывод построчно, как легаси get_pty().")
    testenv: LaunchProfileTestenv = Field(default_factory=LaunchProfileTestenv)
    rerun_script: str | None = Field(
        default=None, max_length=200_000,
        description=(
            "Скрипт повторного запуска для шагов run_mode=rerun: кладётся вместо "
            "starter.sh по тому же пути и запускается той же командой, без клонирования. "
            "Подстановки {{CODE}}. Пусто — rerun-шаги с этим профилем не запускаются."
        ),
    )
    extra_files: list[LaunchProfileExtraFile] = Field(
        default_factory=list, max_length=16,
        description=(
            "Дополнительные файлы на стенде: `tokens.json` FreeIPA, адреса стендов "
            "сценария через `stand_ref` и т.п. Путь — {CODE}, содержимое — {{CODE}}."
        ),
    )


class LaunchProfileVersionResponse(LaunchProfileVersionInput):
    model_config = ConfigDict(from_attributes=True)

    id: str
    profile_id: str
    version: int
    created_by: str | None = None
    created_at: datetime


class LaunchProfileCreate(BaseModel):
    """Новый профиль отдела (или общий — `department_id: null`, только по матрице)."""

    department_id: str | None = Field(default=None, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    is_default: bool = Field(default=False, description="Профиль отдела по умолчанию для тестов без явного профиля.")
    version: LaunchProfileVersionInput


class LaunchProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    is_default: bool | None = None


class LaunchProfileResponse(BaseModel):
    id: str
    department_id: str | None
    name: str
    is_default: bool
    current_version: LaunchProfileVersionResponse | None = None
    created_at: datetime
    updated_at: datetime


class LaunchProfileListResponse(BaseModel):
    items: list[LaunchProfileResponse]
