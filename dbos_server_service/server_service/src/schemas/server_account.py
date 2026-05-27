"""Pydantic-схемы для эндпоинтов /server-accounts.

Аккаунт может быть привязан сразу к нескольким серверам (`server_ids`).
Пароль — общий на все привязанные серверы. На write принимаем `password`
опционально (если не задан — генерим серверной стороной). В GET-карточке
`password_b64` отдаётся только держателю action `view_password`; для
остальных поле остаётся `None`.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.password_policy import validate_password


class ServerAccountCreate(BaseModel):
    """Тело POST /server-accounts. Логин уникален в рамках каждого сервера."""

    server_ids: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Список серверов, к которым привязывается аккаунт (≥1). Все "
            "серверы обязаны принадлежать тому же department'у, что и "
            "вызывающий."
        ),
    )
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
        max_length=512,
        description=(
            "Plaintext пароля. Если не передан — сервер сгенерирует "
            "`secrets.token_urlsafe(32)`. При ручном вводе действует "
            "политика: минимум 8 символов, буквы и цифры."
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

    @field_validator("server_ids")
    @classmethod
    def _dedupe_server_ids(cls, value: list[str]) -> list[str]:
        # Сохраняем порядок, убираем дубли — иначе два одинаковых server_id
        # в одном запросе упёрлись бы в uq_account_server на середине вставки.
        seen: set[str] = set()
        out: list[str] = []
        for sid in value:
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
        return out

    @field_validator("password")
    @classmethod
    def _check_password_policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_password(value)


class ServerAccountUpdate(BaseModel):
    """Тело PATCH /server-accounts/{id}. Пароль через `rotate_password`,
    привязка серверов — через `/servers` под-операции."""

    has_sudo: bool | None = Field(default=None, description="Сменить sudo-флаг (требует `grant_sudo`).")
    unix_groups: list[str] | None = Field(default=None, description="Перезаписать список групп.")
    linked_user_id: str | None = Field(default=None, description="Сменить связь с user'ом.")
    shell: str | None = Field(default=None, max_length=64, description="Сменить shell.")
    home_dir: str | None = Field(default=None, max_length=256, description="Сменить home_dir.")
    is_active: bool | None = Field(default=None, description="Отключить/включить аккаунт.")


class ServerAccountServersUpdate(BaseModel):
    """Тело POST/DELETE /server-accounts/{id}/servers — линковка/отвязка.

    Привязываемые серверы обязаны быть в том же department'е, что и аккаунт.
    Отвязать последний сервер нельзя — аккаунт всегда живёт хотя бы на одном.
    """

    server_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Серверы для привязки/отвязки (≥1).",
    )

    @field_validator("server_ids")
    @classmethod
    def _dedupe_server_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for sid in value:
            if sid not in seen:
                seen.add(sid)
                out.append(sid)
        return out


class ServerAccountResponse(BaseModel):
    """Карточка аккаунта в ответе.

    `server_ids` — список всех привязанных серверов. `password_b64`
    заполняется только когда вызывающий держит action `view_password` —
    тогда это base64(plaintext). У вызывающего без `view_password` (только
    `view`) поле остаётся `None`. Сырого `password_encrypted` в ответе нет
    никогда.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Account ID (prefix acc_).")
    server_ids: list[str] = Field(description="Привязанные серверы.")
    department_id: str = Field(description="Department владельца аккаунта.")
    login: str = Field(description="OS-логин.")
    source: str = Field(
        default="managed",
        description=(
            "Происхождение: `managed` (заведён через API, пароль известен) "
            "или `discovered` (найден инвентаризацией, пароля у API нет)."
        ),
    )
    has_sudo: bool = Field(description="Есть ли sudo.")
    unix_groups: list[str] = Field(description="Unix-группы.")
    linked_user_id: str | None = Field(default=None, description="FK на platform user.")
    shell: str | None = Field(default=None, description="Login shell.")
    home_dir: str | None = Field(default=None, description="Home directory.")
    is_active: bool = Field(description="Активен ли аккаунт.")
    password_rotated_at: datetime | None = Field(
        default=None, description="Когда последний раз ротировался пароль."
    )
    password_b64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded plaintext-пароль. Присутствует только если "
            "вызывающий держит action `view_password`; иначе `null`. "
            "Декодируется стандартным base64.b64decode перед использованием."
        ),
    )
    created_at: datetime = Field(description="Когда аккаунт создан.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
    created_by: str | None = Field(default=None, description="user_id, создавший аккаунт.")


class ServerAccountRotateRequest(BaseModel):
    """Тело POST /server-accounts/{id}/rotate_password.

    `password` опционален: если передан — проходит политику (минимум 8
    символов, буквы и цифры) и используется как новый пароль; если нет —
    сервер генерирует `secrets.token_urlsafe(32)`. Plaintext в ответ не
    возвращается ни в одном случае.
    """

    password: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Plaintext нового пароля. Если пуст — сервер сгенерирует "
            "случайный. При ручном вводе действует политика: минимум 8 "
            "символов, буквы и цифры."
        ),
    )

    @field_validator("password")
    @classmethod
    def _check_password_policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_password(value)


class ServerAccountRotateResponse(BaseModel):
    """Ответ на rotate_password — без plaintext'а наружу."""

    id: str = Field(description="Account ID.")
    login: str = Field(description="OS-логин.")
    rotated_at: datetime = Field(description="UTC timestamp ротации.")


class AccountRotateTask(BaseModel):
    """Одна per-server задача ротации в ответе worker-dispatch'а."""

    server_id: str = Field(description="Сервер, на котором применяется новый пароль.")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")


class AccountRotateSkipped(BaseModel):
    """Сервер, на который задача не поставлена (пропуск при массовой ротации)."""

    server_id: str = Field(description="Сервер, для которого dispatch не выполнен.")
    reason: str = Field(
        description=(
            "Причина пропуска: decommissioned | idempotent_conflict | "
            "worker_unreachable."
        )
    )


class AccountRotateDispatchResponse(BaseModel):
    """Ответ worker-dispatch ротации аккаунта.

    `mode` — `single` (точечная, один сервер) или `all` (массовая, все
    привязанные). `tasks` — по одной задаче на успешно поставленный сервер.
    `skipped` — серверы, на которые задача не поставлена (decommissioned или
    отбита воркером); при массовой ротации один битый сервер не валит весь
    батч.
    """

    mode: str = Field(description="single | all.")
    status: str = Field(default="queued", description="Статус постановки в очередь.")
    tasks: list[AccountRotateTask] = Field(description="Per-server задачи ротации.")
    skipped: list[AccountRotateSkipped] = Field(
        default_factory=list,
        description="Серверы, пропущенные при массовой ротации.",
    )


class AccountProvisionDispatchResponse(BaseModel):
    """Ответ worker-dispatch provision/update/deprovision OS-пользователя.

    `operation` — `provision` (useradd), `update` (usermod) или
    `deprovision` (userdel). `server_id` — сервер, на котором применяется
    операция (один из привязанных). `task_id` — id поставленной задачи.
    """

    operation: str = Field(description="provision | update | deprovision.")
    server_id: str = Field(description="Сервер, на котором применяется операция.")
    task_id: str = Field(description="ID задачи воркера (prefix tsk_).")
    status: str = Field(default="queued", description="Статус постановки в очередь.")
