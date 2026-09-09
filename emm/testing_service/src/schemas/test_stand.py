"""Pydantic-схемы для эндпоинтов /test-stands.

Стенд — надстройка над Server/Vm из server_service (§2.3, §4 плана миграции),
без дублирования их полей. `department_id` намеренно отсутствует в create-
схеме — сервис резолвит его сам живым запросом к server_service, клиент не
может его подделать. `server_id` намеренно отсутствует в update-схеме — смена
привязанного сервера означает создание нового стенда, не правку старого.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TestStandCreate(BaseModel):
    """Тело POST /test-stands. `server_id` уникален — один сервер, один стенд."""

    server_id: str = Field(
        ..., min_length=1, max_length=64,
        description="Server/Vm.id из server_service. UNIQUE.",
    )
    queue_enabled: bool = Field(default=True, description="Участвует ли стенд в очереди тестов.")
    is_active: bool = Field(default=True, description="Активен ли стенд.")


class TestStandUpdate(BaseModel):
    """Тело PATCH /test-stands/{stand_id}. Изменяемы только `queue_enabled`/`is_active`.

    `server_id` в схеме нет — если клиент всё же пришлёт его в теле запроса,
    Pydantic молча отбросит поле как неизвестное, PATCH его не увидит.
    """

    queue_enabled: bool | None = Field(default=None, description="Сменить участие в очереди.")
    is_active: bool | None = Field(default=None, description="Сменить активность.")


class TestStandResponse(BaseModel):
    """Карточка стенда в ответе.

    `server`/`server_unavailable` заполняются только в GET одного стенда
    (живое обогащение карточкой сервера из server_service) — в списке и
    при create/update остаются дефолтными (`None`/`False`).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Test stand ID (prefix stand_).")
    server_id: str = Field(description="Server/Vm.id из server_service.")
    department_id: str = Field(
        description="Отдел-владелец — скопирован с Server.department_id в момент создания стенда.",
    )
    queue_enabled: bool = Field(description="Участвует ли стенд в очереди тестов.")
    is_active: bool = Field(description="Активен ли стенд.")
    created_at: datetime = Field(description="Когда стенд заведён.")
    updated_at: datetime = Field(description="Когда последний раз изменён.")
    created_by: str | None = Field(default=None, description="Кто завёл.")
    server: dict | None = Field(
        default=None,
        description="Живая карточка сервера/ВМ из server_service (только GET одного стенда).",
    )
    server_unavailable: bool = Field(
        default=False,
        description=(
            "Live-вызов к server_service не удался (сервер удалён, сеть "
            "недоступна) — карточка стенда отдана без server-блока."
        ),
    )
