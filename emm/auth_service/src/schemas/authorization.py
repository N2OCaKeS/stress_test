"""Схемы для introspect и проверки service-access."""

from pydantic import BaseModel, Field


class IntrospectRequest(BaseModel):
    """Тело `POST /authorization/introspect`."""
    token: str = Field(description="Токен для валидации (JWT / PAT / bot).")
    caller_ip: str | None = Field(
        default=None,
        description=(
            "IP конечного клиента, который предъявил `token` вызывающему "
            "сервису. Опциональный — но для bot-токенов нужен детектору "
            "`bot.suspicious_multi_ip` (multi-IP алерт). Если сервис не "
            "пробрасывает — детектор тихо пропускает."
        ),
    )


class IntrospectResponse(BaseModel):
    """Ответ introspect — identity + effective роли субъекта.

    `active=False` если токен невалидный/истёкший/отозванный. Остальные поля
    при этом None / пустые. Чувствительные поля (is_banned, allowed_services,
    service_roles) перечитываются из БД, а не берутся из JWT payload.
    """
    active: bool = Field(description="True если токен валиден и владелец не забанен.")
    subject_type: str | None = Field(default=None, description='Тип субъекта: "user", "bot", "oauth_client".')
    is_service_bot: bool = Field(
        default=False,
        description=(
            "True — субъект платформенный сервис-бот (заведён ТОЛЬКО "
            "bootstrap-кодом, например server_worker/testing_service). "
            "Всегда False для user/oauth_client и для обычных ботов, "
            "которых через `POST /bots` заводит себе dep_admin."
        ),
    )
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
    # Принудительная смена пароля — публикуется наружу через introspect, чтобы
    # client-сервисы (loging/secret/...) могли применять свою логику если
    # захотят (например, отказывать в чувствительных операциях до смены).
    # У ботов / oauth_client / не-user субъектов всегда False.
    must_change_password: bool = Field(
        default=False,
        description=(
            "True — у юзера выставлен флаг принудительной смены пароля. "
            "В auth_service'е middleware уже блокирует не-`/me/password` "
            "запросы с PASSWORD_CHANGE_REQUIRED; client-сервисы могут "
            "ограничить свой доступ дополнительно. У bot/PAT/oauth_client "
            "значение всегда False — флаг живёт только на user-row."
        ),
    )


class ServiceAccessRequest(BaseModel):
    """Тело `POST /authorization/service-access`."""
    subject_token: str = Field(description="Токен субъекта, чьи права проверяем.")
    service_name: str = Field(description="Имя сервиса, к которому проверяем access.")


class ServiceAccessResponse(BaseModel):
    """Ответ — есть ли access и какие роли."""
    allowed: bool = Field(description="True если у субъекта есть effective access к сервису.")
    department_id: str | None = None
    service_roles: list[str] = Field(default_factory=list, description="Effective роли субъекта для этого сервиса.")
