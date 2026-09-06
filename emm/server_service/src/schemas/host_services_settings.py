"""Pydantic-схемы настроек SSH-доступа к хосту для host-service control (`/settings/host-services`).

Платформенный singleton под `account_admin`. Приватный ключ никогда не
возвращается в открытом виде — только факт "задан/не задан"
(`private_key_is_set`); обновление идёт write-only полем в PUT.
"""

from pydantic import BaseModel, Field


class HostServicesSettingsResponse(BaseModel):
    """Текущие настройки SSH-доступа к хосту."""

    configured: bool = Field(
        description="True, если host+user+приватный ключ все заданы — статус/control могут пытаться подключаться."
    )
    ssh_host: str | None = Field(default=None, description="Хост (IP/DNS) для SSH-подключения.")
    ssh_port: int = Field(description="Порт SSH.")
    ssh_user: str | None = Field(default=None, description="Имя учётки на хосте (например `emm-host-control`).")
    private_key_is_set: bool = Field(description="Задан ли приватный ключ. Само значение не отдаётся.")


class HostServicesSettingsUpdate(BaseModel):
    """Тело PUT — частичное обновление настроек SSH-доступа к хосту.

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
