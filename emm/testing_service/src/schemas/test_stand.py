"""Pydantic-схемы для эндпоинтов /test-stands.

Стенд — надстройка над Server/Vm из server_service (§2.3, §4 плана миграции),
без дублирования их полей. `department_id` намеренно отсутствует в create-
схеме — сервис резолвит его сам живым запросом к server_service, клиент не
может его подделать. `server_id` намеренно отсутствует в update-схеме — смена
привязанного сервера означает создание нового стенда, не правку старого.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TestStandTestCredentialsResponse(BaseModel):
    """Ответ GET /test-stands/{id}/test-credentials — прокси на server_service (§5.3).

    Зеркалит `server_service.schemas.server.ServerTestCredentialsResponse` —
    сам секрет остаётся у server_service, здесь только pass-through тела его
    ответа. Оба поля с `_b64` заполняются только при `?reveal=true` и наличии
    `view_test_credentials` — тот же гейт, что и на стороне server_service.
    """

    exists: bool = Field(description="Есть ли выпущенная учётка (пайплайн prepare-for-test хоть раз прошёл).")
    username: str | None = Field(default=None, description="OS-логин учётки исполнения теста.")
    ssh_public_key: str | None = Field(default=None, description="Публичный ключ, не секрет.")
    rotated_at: datetime | None = Field(default=None, description="Когда учётка выписана/перевыпущена последний раз.")
    password_b64: str | None = Field(default=None, description="base64(plaintext) пароля — только при reveal=true.")
    ssh_private_key_b64: str | None = Field(
        default=None, description="base64(plaintext PEM) приватного ключа — только при reveal=true.",
    )


class TestStandCreate(BaseModel):
    """Тело POST /test-stands. `server_id`/`vm_id` уникален — один сервер/ВМ, один стенд.

    `target_type=server` + `server_id` (физический стенд, как было) или
    `target_type=vm` + `vm_id` (ВМ server_service). Ровно одно из двух id.
    """

    target_type: Literal["server", "vm"] = Field(
        default="server", description="Тип стенда: физический сервер (ACS) или ВМ (откат снимка).",
    )
    server_id: str | None = Field(
        default=None, min_length=1, max_length=64,
        description="Server.id из server_service. UNIQUE. Для `target_type=server`.",
    )
    vm_id: str | None = Field(
        default=None, min_length=1, max_length=64,
        description="Vm.id из server_service. UNIQUE. Для `target_type=vm`.",
    )
    legacy_token: str | None = Field(
        default=None, max_length=32,
        description=(
            "Имя стенда в allta_app (`stand3`..`stand14`). UNIQUE. Нужно для "
            "совместимости с легаси: имя Zephyr-рана, «№ стенда» в СТП-матрице "
            "и позиционный `-sn` в команде теста. Пусто — стенд заведён уже в emm."
        ),
    )
    queue_enabled: bool = Field(default=True, description="Участвует ли стенд в очереди тестов.")
    is_active: bool = Field(default=True, description="Активен ли стенд.")

    @model_validator(mode="after")
    def _one_target(self) -> "TestStandCreate":
        if self.target_type == "vm":
            if not self.vm_id or self.server_id:
                raise ValueError("target_type=vm requires vm_id and no server_id")
        elif not self.server_id or self.vm_id:
            raise ValueError("target_type=server requires server_id and no vm_id")
        return self


class TestStandUpdate(BaseModel):
    """Тело PATCH /test-stands/{stand_id}. Изменяемы `queue_enabled`/`is_active`/`legacy_token`.

    `server_id` в схеме нет — если клиент всё же пришлёт его в теле запроса,
    Pydantic молча отбросит поле как неизвестное, PATCH его не увидит.
    """

    queue_enabled: bool | None = Field(default=None, description="Сменить участие в очереди.")
    is_active: bool | None = Field(default=None, description="Сменить активность.")
    legacy_token: str | None = Field(
        default=None, max_length=32,
        description="Проставить/сменить легаси-имя стенда (`stand3`..`stand14`).",
    )


class TestStandResponse(BaseModel):
    """Карточка стенда в ответе.

    `server`/`server_unavailable` заполняются только в GET одного стенда
    (живое обогащение карточкой сервера из server_service) — в списке и
    при create/update остаются дефолтными (`None`/`False`).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Test stand ID (prefix stand_).")
    target_type: Literal["server", "vm"] = Field(default="server", description="Тип стенда.")
    server_id: str | None = Field(default=None, description="Server.id из server_service (физический стенд).")
    vm_id: str | None = Field(default=None, description="Vm.id из server_service (ВМ-стенд).")
    department_id: str = Field(
        description="Отдел-владелец — скопирован с Server.department_id в момент создания стенда.",
    )
    legacy_token: str | None = Field(
        default=None, description="Имя стенда в allta_app (`stand3`..`stand14`), если известно.",
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


class TestStandVmSnapshotsResponse(BaseModel):
    """Ответ GET /test-stands/{id}/vm-snapshots — сопоставление снимков ВМ и версий ОС.

    Живой список server_service (`GET /internal/vms/{id}/snapshots`): каждый
    снимок, пригодный для отката, и версия/режим, которые server_service
    прочитал из имени по шаблонам. Отдельной таблицы сопоставления нет (T7).
    """

    stand_id: str
    vm_id: str
    templates: list[str] = Field(description="Шаблоны имени снимка (настройка server_service), по порядку.")
    snapshots: list[dict] = Field(
        description="`name`, `version_name`, `normalized_version`, `mode`, `template`, `kind`, `is_current`.",
    )


class TestStandMetricsItem(BaseModel):
    """Живые CPU/RAM одного стенда — прямой скрейп node_exporter'а (см. `services/stand_metrics.py`).

    Недоступный стенд/порт/exporter — нули, не ошибка: карточка не должна
    гаснуть целиком из-за одного нерабочего стенда в пуле.
    """

    stand_id: str = Field(description="test_stands.id")
    cpu_percent: float = Field(description="Утилизация CPU, % — rate между двумя снятиями node_cpu_seconds_total.")
    ram_percent: float = Field(description="Занятая RAM, % — 1 - MemAvailable/MemTotal.")


class TestStandMetricsResponse(BaseModel):
    """Ответ GET /test-stands/metrics — батч живых CPU/RAM по всем активным стендам отдела."""

    items: list[TestStandMetricsItem]
