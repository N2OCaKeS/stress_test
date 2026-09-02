"""Identity context — зеркалит JWT-claim payload из auth_service.

Объект, который получает каждый endpoint через `Depends(get_current_identity)`.
Содержит то, что вернул auth_service /introspect: user_id, набор ролей по
сервисам, список allowed_services, флаги ban/platform_role.
"""

from pydantic import BaseModel, Field

from src.core.constants import PlatformRole


class IdentityContext(BaseModel):
    """Унифицированный identity-объект — потомок JWT после introspect."""

    user_id: str = Field(description="user_id (sub) — может быть и user'ом, и bot account'ом.")
    username: str = Field(description="Имя для лога/UI (`ivanov`, `server_worker_user` и т.д.).")
    department_id: str | None = Field(default=None, description="Department, к которому привязан caller. None для platform-уровневых ролей.")
    department_name: str | None = Field(default=None, description="Человекочитаемое имя dept (для логов/UI).")
    allowed_services: list[str] = Field(default_factory=list, description="Сервисы, к которым department имеет доступ. server_service должен быть здесь, иначе SERVICE_ACCESS_DENIED.")
    service_roles: dict[str, list[str]] = Field(default_factory=dict, description="Маппинг service_name → список ролей. Эффективные права — union по всем ролям.")
    is_banned: bool = Field(default=False, description="True → каждый запрос отбивается 401 USER_BANNED.")
    platform_role: PlatformRole | None = Field(default=None, description="Platform-роль из enum: account_admin / department_admin / loging_admin / loging_reader. None для обычного юзера.")
    # Тип субъекта: user / bot / pat / oauth_client. Берём из introspect-response,
    # пробрасываем в `audit_service.emit(actor_type=...)`. Без него audit писал
    # бы всё как `actor_type="user"` — worker_bot PAT и OAuth-клиенты смешались
    # бы с человеческими действиями.
    subject_type: str | None = Field(default=None, description='Тип субъекта: "user" / "bot" / "pat" / "oauth_client". None — анонимный/fallback.')

    def roles_for_service(self, service_name: str) -> list[str]:
        """Удобный shortcut на service_roles.get(name, []) — без KeyError."""
        return self.service_roles.get(service_name, [])
