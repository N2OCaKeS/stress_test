"""Схемы для introspect и проверки service-access."""

from pydantic import BaseModel, Field


class IntrospectRequest(BaseModel):
    """Тело `POST /authorization/introspect`."""
    token: str = Field(description="Токен для валидации (JWT / PAT / bot).")


class IntrospectResponse(BaseModel):
    """Ответ introspect — identity + effective роли субъекта.

    `active=False` если токен невалидный/истёкший/отозванный. Остальные поля
    при этом None / пустые. Чувствительные поля (is_banned, allowed_services,
    service_roles) перечитываются из БД, а не берутся из JWT payload.
    """
    active: bool = Field(description="True если токен валиден и владелец не забанен.")
    subject_type: str | None = Field(default=None, description='Тип субъекта: "user", "bot", "oauth_client".')
    sub: str | None = Field(default=None, description="ID субъекта (user_id / bot_id / client_id).")
    username: str | None = None
    department_id: str | None = None
    department_name: str | None = None
    platform_role: str | None = None
    is_banned: bool = Field(default=False, description="True если у юзера активный ban (свежее чтение из БД).")
    allowed_services: list[str] = Field(
        default_factory=list,
        description="Effective allowed_services после INTERSECT.",
    )
    service_roles: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Effective service-роли, отфильтрованные по allowed_services.",
    )
    groups: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Группы субъекта → `<service>.<role>` строки, которые группа даёт. "
            "Группы без service-роли (только access) не показываются."
        ),
    )
    exp: int | None = Field(default=None, description="JWT exp (unix timestamp), если применимо.")


class ServiceAccessRequest(BaseModel):
    """Тело `POST /authorization/service-access`."""
    subject_token: str = Field(description="Токен субъекта, чьи права проверяем.")
    service_name: str = Field(description="Имя сервиса, к которому проверяем access.")


class ServiceAccessResponse(BaseModel):
    """Ответ — есть ли access и какие роли."""
    allowed: bool = Field(description="True если у субъекта есть effective access к сервису.")
    department_id: str | None = None
    service_roles: list[str] = Field(default_factory=list, description="Effective роли субъекта для этого сервиса.")
