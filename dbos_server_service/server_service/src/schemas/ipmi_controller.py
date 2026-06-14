"""Pydantic-схемы для эндпоинтов /servers/{server_id}/ipmi.

`password` принимаем только на write (Create / RotateCredentials). В GET-карточке
`password_b64` отдаётся только держателю action `view_credentials`; для остальных
поле остаётся `None`. Расшифрованные credentials для worker'а отдаются и через
эту же карточку (worker_bot держит `view_credentials`).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.b64 import decode_b64
from src.core.constants import IpmiKind
from src.core.password_policy import validate_password
from src.utils.url_security import validate_safe_endpoint_url


class IpmiControllerCreate(BaseModel):
    """Тело POST /servers/{server_id}/ipmi — регистрация BMC для сервера."""

    kind: IpmiKind = Field(..., description="Тип BMC: idrac / ilo / ipmi / redfish.")
    endpoint_url: str = Field(
        ..., min_length=1, max_length=512,
        description="HTTPS URL Redfish API или IPMI host[:port].",
    )
    username: str = Field(
        ..., min_length=1, max_length=128,
        description="Логин IPMI/iDRAC/iLO-аккаунта.",
    )
    password_b64: str = Field(
        ..., max_length=512,
        description=(
            "Пароль BMC в base64 (`base64.b64encode(plaintext)`). Декодируется "
            "на приёме; к plaintext применяется политика (минимум 8 символов, "
            "буквы и цифры), затем он шифруется через `secrets_service.encrypt()` "
            "ДО записи в БД и в ответе не возвращается. Битый base64 → 422."
        ),
    )

    @field_validator("password_b64")
    @classmethod
    def _check_password_b64(cls, value: str) -> str:
        # Политика — по раскодированному plaintext, не по base64-строке.
        validate_password(decode_b64(value, "password_b64"))
        return value

    @field_validator("endpoint_url")
    @classmethod
    def _check_endpoint_url(cls, value: str) -> str:
        return validate_safe_endpoint_url(value, field_name="endpoint_url")

    def password(self) -> str:
        """Раскодированный plaintext BMC-пароля (валидность уже проверена)."""
        return decode_b64(self.password_b64, "password_b64")


class IpmiControllerUpdate(BaseModel):
    """Тело PATCH /servers/{server_id}/ipmi. Все поля опциональны.

    `password` через PATCH не меняется — для этого `POST /credentials/rotate`,
    чтобы операция шла одним именованным потоком и писалась CRITICAL audit'ом
    отдельно от обычного PATCH-update'а.
    """

    kind: IpmiKind | None = Field(default=None, description="Сменить тип BMC.")
    endpoint_url: str | None = Field(
        default=None, min_length=1, max_length=512,
        description="Сменить endpoint URL.",
    )
    username: str | None = Field(
        default=None, min_length=1, max_length=128,
        description="Сменить login.",
    )

    @field_validator("endpoint_url")
    @classmethod
    def _check_endpoint_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_endpoint_url(value, field_name="endpoint_url")


class IpmiControllerResponse(BaseModel):
    """Карточка контроллера в ответе.

    `password_b64` заполняется только когда вызывающий держит action
    `view_credentials` — тогда это base64(plaintext BMC-пароля). Без
    `view_credentials` (только `view`) поле остаётся `None`. Сырого
    `password_encrypted` в ответе нет никогда.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="IPMI controller ID (prefix ipm_).")
    server_id: str = Field(description="FK на servers.id.")
    kind: str = Field(description="Тип BMC: idrac / ilo / ipmi / redfish.")
    endpoint_url: str = Field(description="HTTPS URL Redfish или IPMI host[:port].")
    username: str = Field(description="Логин IPMI-аккаунта.")
    password_rotated_at: datetime | None = Field(
        default=None, description="UTC момент последней ротации пароля.",
    )
    password_b64: str | None = Field(
        default=None,
        description=(
            "Base64-encoded plaintext BMC-пароля. Присутствует только если "
            "вызывающий держит action `view_credentials`; иначе `null`. "
            "Декодируется стандартным base64.b64decode перед использованием."
        ),
    )
    last_probed_at: datetime | None = Field(
        default=None, description="UTC момент последнего успешного probe BMC.",
    )
    last_status: str | None = Field(
        default=None, description="Результат последнего probe: ok / unreachable / auth_failed.",
    )
    created_at: datetime = Field(description="Когда контроллер зарегистрирован.")
    updated_at: datetime = Field(description="Когда последний раз обновлён.")


class IpmiCredentialsRotateRequest(BaseModel):
    """Тело POST /servers/{server_id}/ipmi/credentials/rotate.

    Endpoint снят (410 GONE) — body больше нигде не декодируется и не
    применяется, схема оставлена ради OpenAPI-описания снятого маршрута.
    `password_b64` опционален: формат — `base64.b64encode(plaintext)`,
    симметрично остальным write-входам.
    """

    password_b64: str | None = Field(
        default=None, max_length=512,
        description=(
            "Новый пароль в base64 (`base64.b64encode(plaintext)`). Если пуст "
            "— сервер сгенерировал бы случайный. Декодируется на приёме; к "
            "раскодированному plaintext применяется политика: минимум 8 "
            "символов, буквы и цифры. Битый base64 → 422."
        ),
    )

    @field_validator("password_b64")
    @classmethod
    def _check_password_b64(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Политика — по раскодированному plaintext, не по base64-строке.
        validate_password(decode_b64(value, "password_b64"))
        return value


class IpmiCredentialsRotateResponse(BaseModel):
    """Ответ ротации. Plaintext наружу не отдаётся."""

    id: str = Field(description="IPMI controller ID.")
    rotated_at: datetime = Field(description="UTC timestamp ротации.")


class IpmiCredentialsViewResponse(BaseModel):
    """Метаданные IPMI-credentials для пользовательского GET'а.

    Plaintext-пароль НЕ возвращается ни при каких условиях — для него только
    internal endpoint (`/internal/.../ipmi/credentials`) под worker'ом.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="IPMI controller ID.")
    server_id: str = Field(description="ID сервера, к которому привязан BMC.")
    kind: str = Field(description="Тип BMC.")
    endpoint_url: str = Field(description="BMC endpoint URL.")
    username: str = Field(description="BMC login.")
    password_rotated_at: datetime | None = Field(
        default=None, description="Когда пароль ротейтился в последний раз (UTC).",
    )


class IpmiPowerStatusCachedResponse(BaseModel):
    """Кэшированный power state, без живого probe.

    Поле `servers.power_state` пишется worker'ом при `power.{on,off,reboot}`
    callback'е; TTL/инвалидации у него нет, поэтому значение может быть
    произвольно устаревшим. Live-опрос доступности — `power.status` worker-task
    через `POST /servers/{id}/power/status` (см. worker_dispatch).
    """

    server_id: str = Field(description="ID сервера.")
    power_state: str = Field(description="on / off / unknown — из server.power_state.")
    last_probed_at: datetime | None = Field(
        default=None,
        description="UTC момент последнего успешного probe BMC (из ipmi_controllers.last_probed_at).",
    )
