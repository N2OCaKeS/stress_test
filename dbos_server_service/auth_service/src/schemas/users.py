"""Схемы CRUD пользователей и назначения ролей."""

from datetime import datetime, timezone

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from src.core.constants import BanType, PlatformRole, UserStatus
from src.core.password_policy import validate_password


class InitialRoleAssignment(BaseModel):
    """Начальная роль для сервиса при создании юзера. Применяется в одной транзакции с user-creation."""
    service_name: str = Field(description="Имя сервиса (`server_service`, ...).")
    roles: list[str] = Field(description="Список role_name из `ServiceRoleDefinition` для этого сервиса.")


class UserCreate(BaseModel):
    """Тело `POST /users`."""
    username: str = Field(min_length=3, max_length=128, description="Уникальный username (3..128 символов).")
    password: str = Field(min_length=8, description="Пароль в plaintext. Минимум 8 символов, буквы + цифры. Хэшируется Argon2id перед записью.")
    email: EmailStr | None = Field(default=None, description="Email (опционально).")
    # У account_admin юзеров нет отдела; для всех остальных ролей department_id обязателен
    department_id: str | None = Field(
        default=None,
        description="ID отдела. Обязателен для всех, кроме account_admin.",
    )
    # `platform_role` — `PlatformRole` enum. Раньше тип был `str | None` и
    # можно было прислать `"hacker"`/`"garbage"` — оно писалось в БД и
    # потом непредсказуемо сравнивалось с `PlatformRole.ACCOUNT_ADMIN` в
    # guard'ах. Сейчас Pydantic режет невалидные → 422.
    platform_role: PlatformRole | None = Field(
        default=None,
        description='Platform-роль: одно из значений `PlatformRole` (account_admin / department_admin / loging_admin / loging_reader). None — обычный юзер.',
    )
    initial_roles: list[InitialRoleAssignment] | None = Field(
        default=None,
        description="Service-роли, которые сразу выдать новому юзеру.",
    )

    @field_validator("password")
    @classmethod
    def _check_password_policy(cls, value: str) -> str:
        return validate_password(value)


class UserUpdate(BaseModel):
    """Тело `PATCH /users/{user_id}` — частичный апдейт."""
    email: EmailStr | None = Field(default=None)
    department_id: str | None = Field(default=None, description="Перевести юзера в другой отдел.")
    # `status` ограничен enum'ом — иначе admin прописал бы `"garbage"`, и
    # `authorization_service.introspect` сравнивал бы это с `UserStatus.ACTIVE`
    # непредсказуемо.
    status: UserStatus | None = Field(default=None, description="Статус юзера (ACTIVE/SUSPENDED/...).")
    # Зеркало `UserCreate.platform_role` — раньше `str | None` пропускал
    # `PATCH {"platform_role": "garbage"}`.
    platform_role: PlatformRole | None = Field(
        default=None,
        description="Сменить platform_role на одно из значений `PlatformRole`.",
    )


class UserResponse(BaseModel):
    """Юзер в ответе list/get эндпоинтов."""
    user_id: str
    username: str
    email: str | None
    department_id: str | None
    department_name: str | None
    status: str
    platform_role: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AssignRolesRequest(BaseModel):
    """Тело `POST /users/{user_id}/roles` — replace-семантика."""
    service_name: str = Field(description="Сервис, для которого выдаём роли.")
    roles: list[str] = Field(description="Новый набор ролей (заменяет текущий). Пустой список = снять все.")


class ResetPasswordRequest(BaseModel):
    """Тело `POST /users/{user_id}/reset-password`."""
    new_password: str = Field(min_length=8, description="Новый пароль (минимум 8 символов, буквы + цифры).")

    @field_validator("new_password")
    @classmethod
    def _check_password_policy(cls, value: str) -> str:
        return validate_password(value)


class SelfChangePasswordRequest(BaseModel):
    """Тело `POST /users/me/password` — self-reset с подтверждением старого пароля.

    Отдельная схема от `ResetPasswordRequest`, потому что у admin-ручки
    `old_password` нет (admin меняет чужой пароль). Сравнение `old != new`
    делается в сервисе через `verify_password` против хэша — нельзя сравнить
    plaintext тут, потому что хэш в БД, а не в схеме.
    """
    old_password: str = Field(
        min_length=1,
        description="Текущий пароль юзера. Проверяется через Argon2id verify.",
    )
    new_password: str = Field(
        min_length=8,
        description="Новый пароль (минимум 8 символов, буквы + цифры).",
    )

    @field_validator("new_password")
    @classmethod
    def _check_password_policy(cls, value: str) -> str:
        return validate_password(value)


class AddUserToGroupRequest(BaseModel):
    """Тело `POST /users/{user_id}/groups`."""
    group_id: str = Field(description="ID группы, в которую добавляем юзера.")


class BanRequest(BaseModel):
    """Тело `POST /users/{user_id}/ban`."""
    # `ban_type` ограничен enum'ом `BanType`. Раньше был `str` — admin клал
    # `"garbage"`, аналитика по типу банов ломалась, а `auto_unban_if_expired`
    # смотрит только `expires_at` (а не тип), так что unknown-типы
    # unban'ились как обычные temporary.
    # Default `PERMANENT` совместим со старым `ban_type: str = "permanent"`
    # и с эндпойнтом, принимавшим body без `ban_type`.
    reason: str | None = Field(default=None, description="Причина бана (произвольный текст).")
    ban_type: BanType = Field(default=BanType.PERMANENT, description="Permanent или temporary.")
    expires_at: datetime | None = Field(
        default=None,
        description="Когда temporary бан истечёт. Обязателен для temporary, запрещён для permanent.",
    )

    @field_validator("expires_at")
    @classmethod
    def _expires_at_must_be_in_future(cls, value: datetime | None) -> datetime | None:
        """`expires_at` должен быть строго в будущем.

        До фикса admin мог создать ban с `expires_at` в прошлом — на первом же
        login юзера `auto_unban_if_expired` снимал ban (т.к. он сразу истёк),
        и фактический ban-effect был нулевой. Bypass через past-expires_at.
        """
        if value is None:
            return value
        # Naive datetimes считаем UTC — Pydantic/FastAPI допускают и naive
        # через `datetime.fromisoformat`, и мы не хотим бить 422 ради формата.
        now = datetime.now(timezone.utc)
        compare = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        if compare <= now:
            # `PydanticCustomError` (вместо raw `ValueError`) нужен потому, что
            # FastAPI default 422-handler пытается JSON-serialize `ctx` объекта
            # ValidationError. Для raw `ValueError` Pydantic кладёт в ctx
            # сам экземпляр `{"error": ValueError(...)}`, который json.dumps
            # не умеет сериализовать → 500 `TypeError: Object of type
            # ValueError is not JSON serializable`. `PydanticCustomError`
            # передаётся как custom_type-строка и сериализуется корректно.
            raise PydanticCustomError(
                "ban_expires_at_in_past",
                "expires_at must be in the future",
            )
        return value

    @model_validator(mode="after")
    def _cross_field_consistency(self) -> "BanRequest":
        """Cross-field инварианты.

        `temporary` требует `expires_at` — иначе ban de-facto permanent
        (auto-unban не сработает), но в БД лежит как temporary → аналитика
        и audit-trail врут. `permanent` не должен иметь `expires_at` —
        это противоречивая конфигурация (permanent с auto-expiry).
        """
        # См. комментарий в `_expires_at_must_be_in_future` — `PydanticCustomError`
        # вместо raw `ValueError` нужен для JSON-serializability error response.
        if self.ban_type == BanType.TEMPORARY and self.expires_at is None:
            raise PydanticCustomError(
                "ban_temporary_requires_expires_at",
                "expires_at is required for temporary ban",
            )
        if self.ban_type == BanType.PERMANENT and self.expires_at is not None:
            raise PydanticCustomError(
                "ban_permanent_forbids_expires_at",
                "expires_at must be null for permanent ban",
            )
        return self


# ── Permissions snapshot (`GET /users/{id}/permissions`) ─────────────────────
#
# Эндпоинт возвращает «единый снимок прав» юзера для UI / admin overview:
# (a) **прямые** service-roles (через `UserServiceRole`),
# (b) **группы** юзера с их service-accesses + service-roles (через
#     `UserGroupMembership` → `UserGroup`),
# (c) **effective** view — `allowed_services` + `service_roles`, посчитанные
#     через `collect_user_permissions` (== INTERSECT с `dept_services ∪
#     group_services`, см. `_merge_permissions`).
#
# Все три слоя отдаются параллельно — UI рендерит admin-page с источником
# каждой роли (direct / group_X), не выводит роли пересчётом из effective
# (effective — merged + INTERSECT, без указания источника). Симметрично с
# `IntrospectResponse` (там только effective layer для service-to-service).


class DirectServiceRoleEntry(BaseModel):
    """Один `UserServiceRole`, выданный напрямую пользователю.

    `assigned_by` может быть `None`, если роль засеяна bootstrap'ом или
    миграцией без attributable actor'а (legacy seed).
    """

    service_name: str = Field(description="Имя сервиса.")
    role_name: str = Field(description="Имя роли (из `ServiceRoleDefinition`).")
    assigned_at: datetime = Field(description="Когда роль была выдана.")
    assigned_by: str | None = Field(default=None, description="Actor, выдавший роль. None для legacy seed.")

    model_config = {"from_attributes": True}


class GroupServiceRoleEntry(BaseModel):
    """Внутри `UserGroupWithRolesEntry.service_roles` — пара (service, role)."""

    service_name: str
    role_name: str


class GroupServiceAccessEntry(BaseModel):
    """Сервис, к которому у группы есть access.

    Без списка ролей — роли отдельно, чтобы UI рендерил матрицу
    access × role без `n×m` дубликатов в JSON.
    """

    service_name: str


class UserGroupWithRolesEntry(BaseModel):
    """Группа, в которой состоит юзер, + её service_accesses и service_roles.

    `department_id` группы инвариантно равен `User.department_id` (см.
    `group_service.add_member` GROUP_DEPARTMENT_MISMATCH check) — поле
    отдаётся отдельно для удобства UI (если в будущем релакснём инвариант).
    """

    group_id: str
    group_name: str
    display_name: str
    department_id: str
    joined_at: datetime
    service_accesses: list[GroupServiceAccessEntry] = Field(default_factory=list)
    service_roles: list[GroupServiceRoleEntry] = Field(default_factory=list)


class UserPermissionsResponse(BaseModel):
    """Полный снимок прав пользователя для UI / admin overview.

    Возвращается ``GET /users/{user_id}/permissions``. Access guard:
    account_admin (любой юзер), department_admin (только свой dept), сам
    юзер (только себя). Иначе — 403.

    Поля:

    * `direct_service_roles` — `UserServiceRole`-строки (is_active=True),
      JOIN с `service_role_definitions` для `role_name`/`service_name`.
    * `groups` — `UserGroupMembership` → `UserGroup` + их service_accesses
      и service_roles. Включает роли сервисов, к которым группа потеряла
      access — INTERSECT их отбрасывает из effective, но в group-секции
      остаются для дебага «почему этой роли нет в effective».
    * `allowed_services` / `service_roles` — effective через
      `collect_user_permissions` (INTERSECT). Для account_admin пуст,
      как в `IdentityContext`.
    """

    user_id: str
    username: str
    department_id: str | None = None
    department_name: str | None = None
    platform_role: PlatformRole | None = None
    is_active: bool
    is_banned: bool
    status: UserStatus

    direct_service_roles: list[DirectServiceRoleEntry] = Field(default_factory=list)
    groups: list[UserGroupWithRolesEntry] = Field(default_factory=list)

    allowed_services: list[str] = Field(default_factory=list)
    service_roles: dict[str, list[str]] = Field(default_factory=dict)

    model_config = {"from_attributes": True}
