"""Pydantic-схемы для /internal lifecycle-эндпоинтов.

Все события приходят от auth_service / account_admin handler'а после того,
как там удалили user'а / dep'а / отозвали department-service-access. Поля
`actor_*` идентифицируют, кто эту операцию инициировал — мы их прокидываем
в audit-эмиты.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.core.constants import SERVICE_NAME


class UserDeletedEvent(BaseModel):
    """`auth_service` сообщает, что user удалён.

    Мы блокируем все его personal-креды, у которых есть RoleACL grantees
    (operator+ admins могут их потом transfer'ить), и просто удаляем
    "сиротские" — некому передать, ACL.count == 0.
    """

    user_id: str = Field(description="ID удалённого пользователя (usr_<hex>).")
    actor_id: str = Field(description="ID actor'а, который выполнил delete_user.")
    actor_username: str = Field(default="", description="Username actor'а — для audit.")


class DeptDeletedEvent(BaseModel):
    """`auth_service` сообщает, что dep удалён.

    Cascade'им сразу обе ветки:
      * dep как owner cred → cred → blocked (account_admin потом transfer'нёт);
      * dep как recipient в DeptGrant → cascade delete grants + RoleACL.
    """

    dept_id: str = Field(description="ID удалённого dep'а (dep_<hex>).")
    actor_id: str = Field(description="ID actor'а, который выполнил delete_dept.")
    actor_username: str = Field(default="", description="Username actor'а — для audit.")


class DeptServiceAccessRevokedEvent(BaseModel):
    """`auth_service` сообщает, что у dep'а отозван доступ к нашему сервису.

    `service` должно быть `secret_service` — иначе обработчик игнорирует
    событие. Cascade сносит все DeptGrant'ы для этого dep'а + RoleACL'и в
    нём. Свои cred'ы dep'а оставляем — у нас всё ещё может быть доступ
    через account_admin transfer'а; их трогает только `delete_dept`.
    """

    dept_id: str = Field(description="ID dep'а (dep_<hex>).")
    service: str = Field(
        description=(
            "Имя сервиса, у которого отозвали access. Только "
            f"{SERVICE_NAME!r} распознаётся; остальные — no-op."
        ),
    )
    actor_id: str = Field(description="ID actor'а, который выполнил revoke.")
    actor_username: str = Field(default="", description="Username actor'а — для audit.")


class LifecycleSummary(BaseModel):
    """Ответ lifecycle-handler'а. Все счётчики необязательны — каждый handler
    заполняет только то, что для него релевантно."""

    blocked_count: int = 0
    deleted_count: int = 0
    dept_grants_revoked: int = 0
    role_acls_revoked: int = 0
    errors: list[str] = Field(default_factory=list)


class MigrationStatus(BaseModel):
    """Прогресс lazy re-encrypt'а под активную версию мастер-ключа.

    Используется ротационным скриптом как гейт `--finalize`: пока
    `remaining_legacy > 0`, удалять старые `SECRET_ENCRYPTION_KEY__v<N>` из
    env'а нельзя — read-path упрётся в ENCRYPTION_KEY_MISSING на не-мигрированных
    строках.

    `outbox_pending_count` — сколько proactive-задач ждёт обработки.
    Финальный гейт — `remaining_legacy == 0 AND outbox_pending_count == 0`.
    """

    active_version: int
    total_rows: int
    by_version: dict[str, int]
    remaining_legacy: int
    migrated_pct: float
    outbox_pending_count: int = 0


class ReencryptOutboxSeedResponse(BaseModel):
    """Сколько pending row'ов опубликовано после `seed`-вызова.

    `inserted` — новые pending row'ы (с учётом partial-UNIQUE).
    `scanned` — credential'ов с legacy-prefix'ом просканировано.
    `active_version` — текущая активная версия мастер-ключа на момент seed'а.
    """

    inserted: int
    scanned: int
    active_version: int


class ReencryptOutboxProcessResponse(BaseModel):
    """Сводка одной process-итерации: сколько перешифровано / упало."""

    processed: int
    errors: int
    failed: list[dict] = Field(default_factory=list)


class ReencryptOutboxStatus(BaseModel):
    """Полная сводка по outbox-таблице — для оператора и monitoring'а."""

    pending: int
    done: int
    error: int
    total: int


class RotateKeyRequest(BaseModel):
    """Тело POST /internal/encryption/rotate."""

    new_key_b64: str = Field(
        min_length=1,
        description=(
            "Новый master-материал в base64 (32 байта после декодирования). "
            "Сгенерить через auth_service `POST /admin/service-keys/generate`."
        ),
    )


class RotateKeyResponse(BaseModel):
    """Ответ POST /internal/encryption/rotate."""

    new_version: int
    previous_version: int
    seeded: ReencryptOutboxSeedResponse
    idempotent: bool


class RetireKeyResponse(BaseModel):
    """Ответ POST /internal/encryption/retire/{version}."""

    version: int
    retired: bool
    remaining_on_version: int = 0
