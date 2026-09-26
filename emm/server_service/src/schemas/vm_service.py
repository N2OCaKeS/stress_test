"""Схемы s2s-канала ВМ-стендов и настройки снимков.

Тела запросов — те же, что у серверного канала (`schemas/internal.py`:
`ServiceAcquireRequest`, `ServiceBusyStatusRequest`, `PreviousHolder`),
ответы — те же поля с `vm_id` вместо `server_id`.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.schemas.internal import PreviousHolder


class VmServiceReservationResponse(BaseModel):
    """Состояние брони ВМ после acquire / release / смены стадии."""

    vm_id: str = Field(description="ID ВМ.")
    busy_state: str = Field(
        description=(
            "Итоговое состояние в терминах `Server.busy_state`: стадия сервисной "
            "брони, `busy` (бронь человека), `updating` (идёт операция над ВМ) или `free`."
        ),
    )
    busy_actor_type: str = Field(description="user / service — кто держит бронь.")
    busy_service_name: str | None = Field(default=None, description="Сервис-держатель.")
    busy_note: str | None = Field(default=None, description="Метка брони (у человеческой — логин).")
    busy_since: datetime | None = Field(default=None, description="С какого момента держится сервисная бронь.")
    previous_holder: PreviousHolder | None = Field(
        default=None, description="Только когда acquire отнял бронь (`takeover=true`).",
    )


class VmConnectionInfoResponse(BaseModel):
    """Ответ GET /internal/vms/{id}/connection-info — адрес гостя для SSH."""

    vm_id: str = Field(description="ID ВМ.")
    host: str = Field(description="`Vm.ip_address` гостя — подключаться по IP.")


class VmTestSnapshotItem(BaseModel):
    """Снимок ВМ, пригодный для отката перед тестом, и как он разобран шаблонами."""

    snapshot_id: str
    name: str = Field(description="Фактическое имя снимка на гипервизоре.")
    kind: str = Field(description="os_baseline / user.")
    os_version: str | None = Field(default=None, description="Версия, записанная в снимке (если известна).")
    snapshot_mode: str | None = Field(default=None, description="Режим, записанный в снимке (orel/smolensk).")
    is_current: bool
    version_name: str | None = Field(
        default=None, description="Версия из имени по шаблону (как в имени). None — имя не подошло ни к одному шаблону.",
    )
    normalized_version: str | None = Field(
        default=None, description="Та же версия после `normalize_os_version_name` — по ней идёт сравнение.",
    )
    mode: str | None = Field(default=None, description="Режим из имени (шаблон с `{mode}`), иначе None — любой.")
    template: str | None = Field(default=None, description="Шаблон, под который подошло имя.")


class VmTestSnapshotListResponse(BaseModel):
    """Ответ GET /internal/vms/{id}/snapshots (вместо `acs-snapshots` у серверов)."""

    snapshots: list[VmTestSnapshotItem]
    templates: list[str] = Field(description="Действующие шаблоны имени снимка, по порядку.")


class VmTestSettingsResponse(BaseModel):
    """Настройки подготовки ВМ-стендов под тест."""

    snapshot_name_templates: list[str] = Field(
        description=(
            "Шаблоны имени снимка ВМ, по порядку. Плейсхолдеры: `{version}` "
            "(ровно один раз), `{mode}`, `{hostname}`, `{vm_name}`."
        ),
    )


class VmTestSettingsUpdate(BaseModel):
    """Тело PUT /settings/vm-test — полная замена списка шаблонов."""

    snapshot_name_templates: list[str] = Field(min_length=1, max_length=10)
