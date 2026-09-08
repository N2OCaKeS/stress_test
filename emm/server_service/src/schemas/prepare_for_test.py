"""Схемы асинхронного контракта `prepare-for-test`.

Три стороны одного контракта:

* `PrepareForTestRequest` / `PrepareForTestAcceptedResponse` — вызов
  `testing_service → server_service` и немедленный 202-ответ;
* `PrepareForTestStatusResponse` — read-модель запроса (наблюдаемость и
  добор результата, если callback не доехал);
* `PrepareForTestCallbackRequest` / `...CallbackResponse` — обратный вызов
  `server_worker → server_service` о завершении новых шагов пайплайна
  (провижн тестового пользователя, смена ядра, ребут+верификация).

Тело самого исходящего callback'а в testing_service лежит не здесь, а
собирается в `services/testing_client.py` — оно не проходит через FastAPI.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PrepareForTestStep = Literal[
    "restore", "prepare", "user_provision", "kernel_change", "mode_switch",
    "reboot_verify",
]
PrepareForTestStatus = Literal["in_progress", "succeeded", "failed"]
PrepareForTestMode = Literal["orel", "smolensk"]


class PrepareForTestRequest(BaseModel):
    """Тело POST /internal/servers/{server_id}/prepare-for-test."""

    os_version_id: str = Field(
        max_length=64,
        description="Версия каталога ОС, из снимка которой восстанавливается стенд.",
    )
    kernel: str = Field(
        max_length=128,
        description=(
            "Ядро, на которое переводится стенд. Обязано быть в "
            "`os_versions.kernels` этой версии — иначе запрос завершается "
            "сразу, `failed_step=kernel_change`, без единого SSH-вызова."
        ),
    )
    mode: PrepareForTestMode = Field(
        description=(
            "Режим безопасности Astra, выставляется между сменой ядра и "
            "финальным ребутом (`astra-modeswitch`). Третье значение "
            "(воронеж) не заводим."
        ),
    )
    test_username: str = Field(
        default="u",
        max_length=32,
        description=(
            "Имя пользователя исполнения теста (per-department настройка на "
            "стороне testing_service, дефолт `u`). Пароль и SSH-ключ ему "
            "выписывает этот пайплайн."
        ),
    )
    requested_by_department_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Отдел, от имени которого идёт подготовка. Если прислан — "
            "сверяется с `server.department_id`, несовпадение маскируется "
            "под 404 SERVER_NOT_FOUND."
        ),
    )
    correlation_id: str = Field(
        max_length=128,
        description=(
            "id очереди/прогона на стороне testing_service. Ключ "
            "идемпотентности: повторный вызов с тем же значением возвращает "
            "уже запущенный запрос, а не стартует второй пайплайн."
        ),
    )


class PrepareForTestAcceptedResponse(BaseModel):
    """202-ответ: пайплайн принят (или уже идёт), результат придёт callback'ом."""

    prepare_request_id: str = Field(
        description="`prep_<hex>` — id запроса, он же сегмент пути callback'а."
    )
    status: PrepareForTestStatus = Field(
        description=(
            "`in_progress` в штатном случае. `failed` — запрос отбит на "
            "входной валидации (например, ядра нет в каталоге РЦ) и "
            "callback уже отправлен; `succeeded` — повтор по "
            "`correlation_id` уже завершённого запроса."
        )
    )


class PrepareForTestStatusResponse(BaseModel):
    """Полное состояние запроса — для наблюдаемости и добора результата."""

    prepare_request_id: str
    server_id: str
    correlation_id: str
    os_version_id: str
    kernel: str
    mode: PrepareForTestMode
    test_username: str
    status: PrepareForTestStatus
    stage: str = Field(description="Шаг, на котором пайплайн сейчас находится.")
    failed_step: PrepareForTestStep | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    callback_attempts: int = Field(
        description="Сколько раз пробовали доставить callback в testing_service."
    )
    callback_delivered_at: datetime | None = None
    callback_last_error: str | None = None


class PrepareForTestCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{server_id}/prepare-for-test-done (от воркера)."""

    prepare_request_id: str = Field(max_length=64)
    succeeded: bool
    failed_step: PrepareForTestStep | None = Field(
        default=None,
        description="Заполняется только при `succeeded=false`.",
    )
    error: str | None = Field(
        default=None,
        max_length=2048,
        description=(
            "При `succeeded=false` — причина провала. При `succeeded=true` "
            "может нести non-fatal предупреждение (например, режим "
            "безопасности перед сменой не совпал с ожидаемым) — это не "
            "провал, но след для расследования уезжает тем же полем."
        ),
    )


class PrepareForTestCallbackResponse(BaseModel):
    """Ответ воркеру: как server_service записал исход."""

    ok: bool = True
    status: PrepareForTestStatus
    callback_delivered: bool = Field(
        description=(
            "Доехал ли исходящий callback в testing_service. False — "
            "потребитель не настроен либо не ответил; результат остался в БД."
        )
    )
