"""Pydantic-схемы для эндпоинтов /server-accounts.

Аккаунт может быть привязан сразу к нескольким серверам (`server_ids`).
Пароль — общий на все привязанные серверы. На write принимаем `password_b64`
опционально (если не задан — генерим серверной стороной) — клиент кодирует
plaintext через `base64.b64encode`, симметрично с reveal-картой, где пароль
отдаётся в `password_b64`. В GET-карточке `password_b64` отдаётся только
держателю action `view_password`; для остальных поле остаётся `None`.
"""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.b64 import decode_b64
from src.core.password_policy import validate_password

# POSIX group name: начинается с lowercase / underscore, дальше цифры / `-`,
# общая длина 32 символа (login.defs default). Те же ограничения дублируются
# в `schemas/internal.py::InventoryUserItem.unix_groups` — расхождение между
# accept-from-API и accept-from-worker привело бы к рассинхрону reconcile.
_POSIX_GROUP_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def _validate_unix_groups(value: list[str]) -> list[str]:
    for name in value:
        if len(name) > 32 or not _POSIX_GROUP_NAME_RE.match(name):
            raise ValueError(
                f"unix_groups: '{name}' не соответствует POSIX group name pattern"
            )
    return value


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
    password_b64: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Пароль в base64 (`base64.b64encode(plaintext)`). Если не передан "
            "— сервер сгенерирует `secrets.token_urlsafe(32)`. Декодируется на "
            "приёме; к раскодированному plaintext применяется политика: "
            "минимум 8 символов, буквы и цифры. Битый base64 → 422."
        ),
    )
    has_sudo: bool = Field(
        default=False,
        description="Право sudo. Требует отдельного action `grant_sudo` (admin-only).",
    )
    unix_groups: list[str] = Field(
        default_factory=list,
        max_length=64,
        description=(
            "Список Unix-групп аккаунта. Имена валидируются POSIX-паттерном; "
            "существование групп на боксе не проверяется — это задача worker'а."
        ),
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

    @field_validator("password_b64")
    @classmethod
    def _check_password_b64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Политика проверяется по РАСКОДИРОВАННОМУ паролю, не по base64-строке.
        validate_password(decode_b64(value, "password_b64"))
        return value

    @field_validator("unix_groups")
    @classmethod
    def _check_unix_groups(cls, value: list[str]) -> list[str]:
        return _validate_unix_groups(value)

    def password(self) -> str | None:
        """Раскодированный plaintext пароля (или `None`, если не передан).

        Валидность base64 и политика уже проверены валидатором — здесь только
        повторный декод для сервис-слоя.
        """
        if self.password_b64 is None:
            return None
        return decode_b64(self.password_b64, "password_b64")


class ServerAccountUpdate(BaseModel):
    """Тело PATCH /server-accounts/{id}. Пароль через `rotate_password`,
    привязка серверов — через `/servers` под-операции.

    Поля `is_active` в апдейте нет: колонка в БД присутствует и отдаётся
    в GET, но dispatch/rotate/fetch_password её не читают, и менять её
    через PATCH ведёт только к расхождению между «логически выключенным»
    аккаунтом и тем фактом, что воркер всё равно отдаст пароль. Когда
    появится реальная семантика disable — поле вернётся вместе с гейтами
    в internal_service и pipeline'ах.
    """

    has_sudo: bool | None = Field(default=None, description="Сменить sudo-флаг (требует `grant_sudo`).")
    unix_groups: list[str] | None = Field(
        default=None,
        max_length=64,
        description="Перезаписать список групп (валидируется POSIX-паттерном).",
    )
    linked_user_id: str | None = Field(default=None, description="Сменить связь с user'ом.")
    shell: str | None = Field(default=None, max_length=64, description="Сменить shell.")
    home_dir: str | None = Field(default=None, max_length=256, description="Сменить home_dir.")

    @field_validator("unix_groups")
    @classmethod
    def _check_unix_groups(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _validate_unix_groups(value)


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

    `password_b64` опционален: если передан — `base64.b64encode(plaintext)`,
    декодируется на приёме, к plaintext применяется политика (минимум 8
    символов, буквы и цифры) и он используется как новый пароль; если нет —
    сервер генерирует `secrets.token_urlsafe(32)`. Plaintext в ответ не
    возвращается ни в одном случае.
    """

    password_b64: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Новый пароль в base64 (`base64.b64encode(plaintext)`). Если пуст "
            "— сервер сгенерирует случайный. Декодируется на приёме; к "
            "раскодированному plaintext применяется политика: минимум 8 "
            "символов, буквы и цифры. Битый base64 → 422."
        ),
    )

    @field_validator("password_b64")
    @classmethod
    def _check_password_b64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Политика — по раскодированному plaintext, не по base64-строке.
        validate_password(decode_b64(value, "password_b64"))
        return value

    def password(self) -> str | None:
        """Раскодированный plaintext нового пароля (или `None`)."""
        if self.password_b64 is None:
            return None
        return decode_b64(self.password_b64, "password_b64")


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

    `partial_failure` помечен явно для UX-consistency: `False` означает,
    что весь батч пролетел (или пропуски — это idempotent/decommissioned,
    они в `skipped`). `True` поднимается, когда в массовом режиме worker
    отбил ServiceUnavailable после K успешных dispatch'ей — тогда K задач
    уже в очереди, остаток не пытались. `next_action` подсказывает UI,
    что делать дальше: `retry_not_attempted` (повторить запрос после
    стабилизации воркера) или `manual_cancel_dispatched` (отменить уже
    поставленные через `/tasks/{id}/cancel`, если откатить ротацию важно).
    """

    mode: str = Field(description="single | all.")
    status: str = Field(default="queued", description="Статус постановки в очередь.")
    tasks: list[AccountRotateTask] = Field(description="Per-server задачи ротации.")
    skipped: list[AccountRotateSkipped] = Field(
        default_factory=list,
        description="Серверы, пропущенные при массовой ротации.",
    )
    partial_failure: bool = Field(
        default=False,
        description=(
            "True, если массовая ротация частично применилась (worker отбил "
            "после K успешных dispatch'ей). Auto-cancel не выполняется — UI "
            "должен показать `tasks` (уже dispatched) и `next_action`."
        ),
    )
    next_action: str | None = Field(
        default=None,
        description=(
            "Подсказка UI на случай partial_failure: `retry_not_attempted` "
            "(повторить весь запрос) или `manual_cancel_dispatched` "
            "(отменить уже поставленные task_ids). None — partial_failure=False."
        ),
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
