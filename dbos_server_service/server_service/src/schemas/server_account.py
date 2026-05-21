"""Pydantic-схемы для эндпоинтов /server-accounts.

Пароли наружу не отдаём ни в одном response'е. На write — принимаем
`password` опционально (если не задан, генерим серверной стороной).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ServerAccountCreate(BaseModel):
    """Тело POST /server-accounts. Логин уникален в рамках сервера."""

    server_id: str = Field(..., description="FK на servers.id — сервер, для которого создаётся аккаунт.")
    # Regex держим в sync с `server_worker/src/clients/ssh.py::_LOGIN_RE` —
    # там идёт повторная валидация перед `chpasswd`, чтобы воркер не зависел
    # от того, дошёл ли request через эту схему. Если меняешь pattern —
    # меняй и там, и обнови тесты в обоих сервисах.
    login: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._\-]+$",
        description=(
            "Имя OS-аккаунта (root/postgres/...). "
            "Только буквы/цифры/`.`/`_`/`-` — защита от CRLF и shell-инъекций "
            "в audit details и в `chpasswd` payload."
        ),
    )
    password: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description=(
            "Plaintext пароля. Если не передан — сервер сгенерирует "
            "`secrets.token_urlsafe(32)`."
        ),
    )
    has_sudo: bool = Field(
        default=False,
        description="Право sudo. Требует отдельного action `grant_sudo` (admin-only).",
    )
    unix_groups: list[str] = Field(
        default_factory=list, description="Список Unix-групп аккаунта (без проверки существования)."
    )
    linked_user_id: str | None = Field(
        default=None, description="Опциональный FK на платформенного user'а (для DBoS-аккаунтов)."
    )
    shell: str | None = Field(
        default=None, max_length=64, description="Login shell (/bin/bash и т.п.)."
    )
    home_dir: str | None = Field(
        default=None, max_length=256, description="Путь home-директории."
    )


class ServerAccountUpdate(BaseModel):
    """Тело PATCH /server-accounts/{id}. Пароль через `rotate_password`."""

    has_sudo: bool | None = Field(default=None, description="Сменить sudo-флаг (требует `grant_sudo`).")
    unix_groups: list[str] | None = Field(default=None, description="Перезаписать список групп.")
    linked_user_id: str | None = Field(default=None, description="Сменить связь с user'ом.")
    shell: str | None = Field(default=None, max_length=64, description="Сменить shell.")
    home_dir: str | None = Field(default=None, max_length=256, description="Сменить home_dir.")
    is_active: bool | None = Field(default=None, description="Отключить/включить аккаунт.")


class ServerAccountResponse(BaseModel):
    """Карточка аккаунта в ответе. БЕЗ password/encrypted-token."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Account ID (prefix acc_).")
    server_id: str = Field(description="FK на server.")
    login: str = Field(description="OS-логин.")
    has_sudo: bool = Field(description="Есть ли sudo.")
    unix_groups: list[str] = Field(description="Unix-группы.")
    linked_user_id: str | None = Field(default=None, description="FK на platform user.")
    shell: str | None = Field(default=None, description="Login shell.")
    home_dir: str | None = Field(default=None, description="Home directory.")
    is_active: bool = Field(description="Активен ли аккаунт.")
    password_rotated_at: datetime | None = Field(
        default=None, description="Когда последний раз ротировался пароль."
    )
    created_at: datetime = Field(description="Когда аккаунт создан.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
    created_by: str | None = Field(default=None, description="user_id, создавший аккаунт.")


class ServerAccountRotateResponse(BaseModel):
    """Ответ на rotate_password — без plaintext'а наружу."""

    id: str = Field(description="Account ID.")
    login: str = Field(description="OS-логин.")
    rotated_at: datetime = Field(description="UTC timestamp ротации.")


class RevealPasswordResponse(BaseModel):
    """Ответ на reveal-password — plaintext-пароль в base64.

    Сам plaintext возвращается base64-encoded (standard b64), чтобы не
    зависеть от транспортного слоя в отношении бинарных байт. UI/CLI
    декодирует обратно непосредственно перед показом/использованием.
    """

    password_b64: str = Field(
        description=(
            "Base64-encoded plaintext-пароль OS-аккаунта. Получатель обязан "
            "декодировать через стандартный base64.b64decode перед выводом."
        ),
    )
