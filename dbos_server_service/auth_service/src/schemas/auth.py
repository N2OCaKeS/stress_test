"""Схемы для login / refresh / logout / identity-контекста."""

from pydantic import BaseModel, Field


class IdentityContext(BaseModel):
    """Identity юзера/бота — то, что возвращает `/me` и embed'ится в introspect.

    Чувствительные поля (`is_banned`, `allowed_services`, `service_roles`) НЕ
    лежат в JWT payload — берутся всегда свежие из БД через `collect_user_permissions`.
    """

    user_id: str = Field(description="Уникальный ID юзера или бота")
    username: str = Field(description="Username (для бота — bot name)")
    department_id: str | None = Field(default=None, description="ID отдела. None у account_admin.")
    department_name: str | None = Field(default=None, description="Человеческое название отдела (display_name).")
    allowed_services: list[str] = Field(
        default_factory=list,
        description="Список service_name, к которым у юзера есть effective access (после INTERSECT).",
    )
    service_roles: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Effective роли по сервисам: `{service_name: [role_name, ...]}`.",
    )
    is_banned: bool = Field(default=False, description="True, если активен `Ban` (permanent или не истёкший temporary).")
    platform_role: str | None = Field(
        default=None,
        description="Platform-уровень: `account_admin` или `department_admin`. None у обычного юзера.",
    )
    # Тип actor-а за этим identity: ``"user"`` (default — JWT минтуется логином
    # или PAT) или ``"oauth_client"`` (m2m JWT через client_credentials).
    # Используется ``require_user_context``-guard'ом для отсечения m2m-JWT на
    # user-facing endpoint'ах (см. ``dependencies/auth.py``). Default — "user"
    # для обратной совместимости с тестовыми payload'ами, где поле может не
    # выставляться явно.
    subject_type: str = Field(
        default="user",
        description='Тип субъекта: "user" (обычный юзер/PAT/bot) или "oauth_client" (m2m).',
    )


class LoginRequest(BaseModel):
    """Тело `POST /login` — логин по username/password."""
    # `min_length=1` — Pydantic вернёт 422 на пустую строку до того, как мы
    # успеем поджечь Argon2id-verify на dummy-хэше. `max_length` режет
    # очевидный DoS-вектор (мегабайтный username в JSON body).
    username: str = Field(min_length=1, max_length=255, description="Username")
    password: str = Field(min_length=1, max_length=1024, description="Пароль в plaintext (TLS обязателен)")


class LoginResponse(BaseModel):
    """Ответ `POST /login` — пара токенов + identity-контекст."""
    access_token: str = Field(description="Короткоживущий JWT (~10 мин). Bearer.")
    refresh_token: str = Field(description="Opaque refresh, в БД только hash. Используй для `/refresh`.")
    token_type: str = Field(default="Bearer", description='Всегда "Bearer".')
    expires_in: int = Field(description="TTL access-токена в секундах")
    identity: IdentityContext = Field(description="Свежий identity-контекст юзера")


class RefreshRequest(BaseModel):
    """Тело `POST /refresh` — обмен refresh на новую пару."""
    refresh_token: str = Field(description="Текущий refresh. После ротации станет невалидным.")


class RefreshResponse(BaseModel):
    """Ответ `POST /refresh` — новая пара access + refresh."""
    access_token: str = Field(description="Новый access JWT")
    refresh_token: str = Field(description="Новый refresh (старый уже инвалидирован)")
    token_type: str = Field(default="Bearer")
    expires_in: int = Field(description="TTL access-токена в секундах")


class LogoutRequest(BaseModel):
    """Тело `POST /logout` — инвалидация refresh."""
    refresh_token: str = Field(description="Refresh, который надо отозвать")
