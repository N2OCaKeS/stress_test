"""Pydantic-схемы настроек SSH-доступа к хосту (host-service control) и списка юнитов.

Per-department: `department_id` нигде не принимается от caller'а — эндпоинт
всегда резолвит его из identity (см. `api/v1/endpoints/host_services_settings.py`).
Приватный ключ никогда не возвращается в открытом виде — только факт
"задан/не задан" (`private_key_is_set`); обновление идёт write-only полем в PUT.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class HostServicesSettingsResponse(BaseModel):
    """Текущие настройки SSH-доступа к хосту своего отдела."""

    configured: bool = Field(
        description="True, если host+user+приватный ключ все заданы — статус/control могут пытаться подключаться."
    )
    ssh_host: str | None = Field(default=None, description="Хост (IP/DNS) для SSH-подключения.")
    ssh_port: int = Field(description="Порт SSH.")
    ssh_user: str | None = Field(default=None, description="Имя учётки на хосте (например `emm-host-control`).")
    private_key_is_set: bool = Field(description="Задан ли приватный ключ. Само значение не отдаётся.")


class HostServicesSettingsUpdate(BaseModel):
    """Тело PUT — частичное обновление настроек SSH-доступа к хосту своего отдела.

    Любое поле можно опустить — тогда текущее значение сохраняется. Пустая
    строка в `ssh_host`/`ssh_user` трактуется как явная очистка (администратор
    стирает поле в форме) — та же семантика, что у `acs_url` в `AcsSettingsUpdate`.
    `ssh_private_key` — plaintext PEM/OpenSSH-ключ на вход, шифруется на
    сервисном слое перед сохранением; пустое/отсутствующее значение не трогает
    уже сохранённый ключ. Чтобы явно стереть ключ — `clear_private_key=True`.
    """

    ssh_host: str | None = Field(default=None, max_length=255, description="Хост (IP/DNS) для SSH-подключения.")
    ssh_port: int | None = Field(default=None, ge=1, le=65535, description="Порт SSH.")
    ssh_user: str | None = Field(default=None, max_length=64, description="Имя учётки на хосте.")
    ssh_private_key: str | None = Field(
        default=None,
        description="Новый приватный SSH-ключ (plaintext, PEM/OpenSSH). Пусто — не менять текущий.",
    )
    clear_private_key: bool = Field(
        default=False,
        description="Явно стереть сохранённый приватный ключ (игнорируется, если одновременно передан `ssh_private_key`).",
    )


class HostServiceUnitResponse(BaseModel):
    """Один systemd-юнит из списка отдела."""

    id: str = Field(description="`hsu_<uuid>`.")
    unit_name: str = Field(description="Базовое имя юнита без `.service`, например `acs`.")
    label: str = Field(description="Display-имя (по умолчанию равно `unit_name`).")
    created_at: datetime = Field(description="Момент добавления, UTC.")
    created_by: str | None = Field(default=None, description="user_id добавившего.")


class HostServiceUnitListResponse(BaseModel):
    """Ответ `GET /settings/host-services/units` — весь список юнитов отдела."""

    items: list[HostServiceUnitResponse] = Field(default_factory=list)


class HostServiceUnitCreate(BaseModel):
    """Тело `POST /settings/host-services/units`."""

    unit_name: str = Field(
        min_length=1,
        max_length=128,
        description="Базовое имя юнита (без `.service` — оно отбрасывается, если передано). Разрешён charset `[a-zA-Z0-9_.@-]+`.",
    )
    label: str | None = Field(
        default=None, max_length=128, description="Display-имя. Пусто — берётся равным `unit_name`."
    )


class HostServiceUnitUpdate(BaseModel):
    """Тело `PATCH /settings/host-services/units/{unit_id}` — переименование label.

    `unit_name` не редактируется post-creation осознанно: смена имени юнита —
    это фактически другой юнит на хосте (и другая строка в sudoers), проще и
    безопаснее удалить и добавить заново, чем разбираться с частично
    рассинхронизированным состоянием "переименовали в БД, а sudoers/guard на
    хосте всё ещё под старым именем".
    """

    label: str = Field(min_length=1, max_length=128, description="Новое display-имя.")
