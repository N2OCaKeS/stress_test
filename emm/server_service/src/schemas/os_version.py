"""Pydantic-схемы для эндпоинтов /os-versions.

OS-версии — глобальный каталог. Read публичный (без auth), CRUD — под матрицей прав.
"""

from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Каталог версий — это пара десятков apt/yum-репозиториев на запись, не больше.
_MAX_REPOSITORIES = 64
_MAX_REPOSITORY_URL_LEN = 2048


def _is_http_url(value: str) -> bool:
    """True для непустого http(s)-URL с хостом."""
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _validate_repositories(value: list[str] | None) -> list[str] | None:
    """Каждый элемент — голый http(s)-URL либо строка sources.list.

    Резолвер репозиториев (`os_version_repo_resolver`) кладёт сюда строки вида
    `deb <url> <suite> <components...>`, поэтому кроме голого URL принимаем и
    deb/deb-src-строку, второй токен которой — валидный http(s)-URL. Оба формата
    в разумных лимитах.
    """
    if value is None:
        return value
    if len(value) > _MAX_REPOSITORIES:
        raise ValueError(f"too many repositories (max {_MAX_REPOSITORIES})")
    for item in value:
        if not isinstance(item, str):
            raise ValueError("repository must be a string")
        if len(item) > _MAX_REPOSITORY_URL_LEN:
            raise ValueError(f"repository URL too long (max {_MAX_REPOSITORY_URL_LEN})")
        stripped = item.strip()
        if stripped.startswith(("deb ", "deb-src ")):
            parts = stripped.split()
            if len(parts) < 3 or not _is_http_url(parts[1]):
                raise ValueError(f"repository is not a valid sources.list line: {item!r}")
            continue
        if not _is_http_url(stripped):
            raise ValueError(f"repository must be a valid http(s) URL: {item!r}")
    return value


# Список ядер ведётся вручную, тех же порядков, что и repositories.
_MAX_KERNELS = 128
_MAX_KERNEL_LEN = 64


def _validate_kernels(value: list[str] | None) -> list[str] | None:
    """Каждый элемент — непустая строка разумной длины (версия ядра)."""
    if value is None:
        return value
    if len(value) > _MAX_KERNELS:
        raise ValueError(f"too many kernels (max {_MAX_KERNELS})")
    for item in value:
        if not isinstance(item, str):
            raise ValueError("kernel must be a string")
        stripped = item.strip()
        if not stripped:
            raise ValueError("kernel must not be empty")
        if len(stripped) > _MAX_KERNEL_LEN:
            raise ValueError(f"kernel too long (max {_MAX_KERNEL_LEN})")
    return value


_MAX_BUILD_VERSION_LEN = 64


def _validate_build_version(value: str | None) -> str | None:
    """Build-версия: минимум три dot-сегмента, непустые части, разумная длина.

    `1.7.5.6` и легаси `1.7.3.UU.1` валидны; `1.7` — нет.
    """
    if value is None:
        return value
    stripped = value.strip()
    if not stripped:
        raise ValueError("build_version must not be empty")
    if len(stripped) > _MAX_BUILD_VERSION_LEN:
        raise ValueError(f"build_version too long (max {_MAX_BUILD_VERSION_LEN})")
    parts = stripped.split(".")
    if len(parts) < 3 or any(not p for p in parts):
        raise ValueError("build_version must look like X.Y.Z or X.Y.Z.W")
    return stripped


class OsVersionCreate(BaseModel):
    """Тело POST /os-versions. `name` уникален."""

    name: str = Field(
        ..., min_length=1, max_length=128,
        description="Каноническое имя версии (astra-1.7, ubuntu-22.04, ...). UNIQUE.",
    )
    description: str | None = Field(
        default=None, description="Произвольное описание для UI/каталога.",
    )
    repositories: list[str] = Field(
        default_factory=list,
        description="URL-адреса репозиториев версии (apt/yum/...).",
    )
    kernels: list[str] = Field(
        default_factory=list,
        description="Версии ядер, доступные для этой версии. Список редактируется вручную.",
    )
    is_urgent_update: bool = Field(
        default=False,
        description="Срочный хотфикс вне обычного цикла РЦ (legacy UU), а не плановый релиз.",
    )
    build_version: str | None = Field(
        default=None,
        description=(
            "Build-версия ОС (X.Y.Z.W). Если задана и repositories пуст — сервис "
            "сам построит repo-строки из индекса релизов. Само значение не "
            "хранится, только результат резолва."
        ),
    )

    @field_validator("repositories")
    @classmethod
    def _check_repositories(cls, value: list[str]) -> list[str]:
        return _validate_repositories(value)

    @field_validator("kernels")
    @classmethod
    def _check_kernels(cls, value: list[str]) -> list[str]:
        return _validate_kernels(value)

    @field_validator("build_version")
    @classmethod
    def _check_build_version(cls, value: str | None) -> str | None:
        return _validate_build_version(value)


class OsVersionUpdate(BaseModel):
    """Тело PATCH /os-versions/{os_version_id}. Все поля опциональны."""

    name: str | None = Field(
        default=None, min_length=1, max_length=128, description="Сменить имя (UNIQUE).",
    )
    description: str | None = Field(default=None, description="Сменить описание.")
    repositories: list[str] | None = Field(
        default=None,
        description="Заменить список репозиториев целиком.",
    )
    kernels: list[str] | None = Field(
        default=None,
        description="Заменить список ядер целиком.",
    )
    is_urgent_update: bool | None = Field(
        default=None,
        description="Сменить флаг срочного хотфикса (legacy UU).",
    )

    @field_validator("repositories")
    @classmethod
    def _check_repositories(cls, value: list[str] | None) -> list[str] | None:
        return _validate_repositories(value)

    @field_validator("kernels")
    @classmethod
    def _check_kernels(cls, value: list[str] | None) -> list[str] | None:
        return _validate_kernels(value)


class OsVersionResolveRequest(BaseModel):
    """Тело POST /os-versions/{id}/resolve-repositories.

    Перестроить `repositories` версии из индекса релизов по build-версии.
    """

    build_version: str = Field(
        ...,
        description="Build-версия ОС (X.Y.Z.W), по которой резолвятся repo-строки.",
    )

    @field_validator("build_version")
    @classmethod
    def _check_build_version(cls, value: str) -> str:
        return _validate_build_version(value)


class OsVersionBootstrapPasswordStatus(BaseModel):
    """Ответ GET /os-versions/{id}/bootstrap-password.

    Пароль никогда не отдаётся — только логин и факт "задан/не задан".
    `None` (нет строки в БД) отдаётся как `has_password=False`, `ssh_username`
    в этом случае тоже `None`.
    """

    ssh_username: str | None = Field(
        default=None, description="Логин bootstrap-пользователя образа."
    )
    has_password: bool = Field(description="Задан ли пароль для этой версии.")


class OsVersionBootstrapPasswordInternalResponse(BaseModel):
    """Ответ GET /internal/os-versions/{id}/bootstrap-password — только worker_bot.

    В отличие от `OsVersionBootstrapPasswordStatus` (публичный статус, без
    пароля), это internal-эндпоинт и ОТДАЁТ plaintext — worker'у нужно
    реально залогиниться по SSH, чтобы подтвердить готовность сервера перед
    тем, как репортить ACS restore успешным.
    """

    ssh_username: str = Field(description="Логин bootstrap-пользователя образа.")
    password: str = Field(description="Расшифрованный пароль bootstrap-пользователя.")


class OsVersionBootstrapPasswordUpdate(BaseModel):
    """Тело PUT /os-versions/{id}/bootstrap-password.

    Plaintext на вход (симметрично остальным creds-полям server_service) —
    шифруется на сервисном слое перед сохранением. Upsert: и логин, и пароль
    обязательны, частичного обновления нет (пароль зашит вместе с логином).
    """

    ssh_username: str = Field(
        ..., min_length=1, max_length=128,
        description="Логин bootstrap-пользователя образа, которым восстанавливается сервер.",
    )
    password: str = Field(
        ..., min_length=1,
        description="Пароль bootstrap-пользователя (plaintext на вход).",
    )


class OsVersionResponse(BaseModel):
    """Карточка OS-версии в ответе."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="OS version ID (prefix osv_).")
    name: str = Field(description="Имя версии.")
    description: str | None = Field(default=None, description="Описание.")
    repositories: list[str] = Field(
        default_factory=list, description="URL-адреса репозиториев версии.",
    )
    kernels: list[str] = Field(
        default_factory=list, description="Версии ядер, доступные для этой версии.",
    )
    is_urgent_update: bool = Field(
        default=False, description="Срочный хотфикс вне обычного цикла РЦ (legacy UU).",
    )
    discovered_at: datetime = Field(description="Когда версия добавлена в каталог.")
    updated_at: datetime = Field(description="Когда последний раз изменена.")
