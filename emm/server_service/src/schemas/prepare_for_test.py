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
from typing import Annotated, Literal

from pydantic import BaseModel, Field

PrepareForTestStep = Literal[
    "restore", "vm_revert", "prepare", "user_provision", "pam_fix", "stand_setup",
    "kernel_change", "mode_switch", "reboot_verify",
]
StandSetupStep = Literal["pam_fix", "stand_setup", "reboot_verify"]
PrepareForTestStatus = Literal["in_progress", "succeeded", "failed"]
PrepareForTestMode = Literal["orel", "smolensk"]
PrepareForTestPreparation = Literal["full", "revert_only"]


_CMDLINE_RE = r"^[A-Za-z0-9._,:=/+-]{1,128}$"


class StandSetupSpec(BaseModel):
    """Шаг настройки стенда теста."""

    kernel_cmdline_extra: list[Annotated[str, Field(pattern=_CMDLINE_RE)]] = Field(
        default_factory=list, max_length=32,
        description="Доп. параметры ядра в GRUB_CMDLINE_LINUX_DEFAULT (идемпотентно).",
    )
    script: str = Field(
        default="", max_length=200_000,
        description="Bash-скрипт, уже отрезолвленный testing_service. Хранится зашифрованным.",
    )
    script_is_sensitive: bool = Field(default=False, description="Не писать вывод скрипта в лог подготовки.")
    run_as: Literal["root", "test_user"] = "root"
    phase: Literal["before_kernel", "after_boot"] = "after_boot"
    reboot_after: bool = True
    timeout_seconds: int = Field(default=1800, ge=10, le=86400)


class ProvisioningSpec(BaseModel):
    """Профиль подготовки: значения, а не id — профилей здесь нет."""

    allowed_failed_units: list[Annotated[str, Field(max_length=256)]] = Field(
        default_factory=lambda: ["astra-mount-lock.service"], max_length=64,
    )
    degraded_reboot_attempts: int = Field(default=3, ge=0, le=20)
    disable_pam_lastlog_inactive: bool = True
    boot_wait_timeout_seconds: int | None = Field(default=None, ge=60, le=86400)


class PrepareForTestTarget(BaseModel):
    """Цель подготовки: сервер или ВМ.

    Сама цель задаётся путём запроса (`/internal/servers/{id}/…` или
    `/internal/vms/{id}/…`); поле в теле — подтверждение вызывающего,
    расхождение с путём отбивается 422 `PREPARE_TARGET_MISMATCH`.
    """

    type: Literal["server", "vm"] = "server"
    vm_id: str | None = Field(default=None, max_length=64)


class PrepareForTestRequest(BaseModel):
    """Тело POST /internal/servers/{server_id}/prepare-for-test
    (и `/internal/vms/{vm_id}/prepare-for-test`)."""

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
            "выписывает этот пайплайн. Игнорируется, если задан "
            "`test_account_credential_id`."
        ),
    )
    test_account_credential_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Тестовая учётка отдела в secret_service (credential "
            "scope=service, service=test_account, отдел-владелец — отдел "
            "сервера). Если задана — логин, пароль и публичный ключ берутся "
            "из неё (раскрывается на шаге user_provision, так что смена "
            "учётки действует со следующей подготовки), `test_username` "
            "игнорируется, случайные пароль/ключ не генерируются, и callback "
            "учётных данных не несёт. Не задана — прежнее поведение."
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
    stand_setup: StandSetupSpec | None = Field(
        default=None, description="Шаг настройки стенда теста.",
    )
    provisioning: ProvisioningSpec | None = Field(
        default=None,
        description="Профиль подготовки; нет — прежнее ожидание (только `running`), без PAM-правки.",
    )
    target: PrepareForTestTarget | None = Field(
        default=None,
        description="Цель: `{type: server}` по умолчанию или `{type: vm, vm_id}`.",
    )
    preparation: PrepareForTestPreparation = Field(
        default="full",
        description=(
            "`full` — все шаги. `revert_only` — restore/откат снимка, учётка "
            "и ядро без смены режима безопасности и без шага настройки стенда "
            "(`stand_setup` в теле игнорируется). `mode` при этом всё равно "
            "обязателен: по нему выбирается снимок ВМ."
        ),
    )
    skip_pam_fix: bool = Field(
        default=False,
        description=(
            "Не выполнять шаг `pam_fix` (`pam_lastlog.so inactive=`) независимо "
            "от `provisioning.disable_pam_lastlog_inactive`. Не зависит от "
            "`preparation`."
        ),
    )


class StandSetupRequest(BaseModel):
    """Тело POST /internal/servers/{server_id}/stand-setup (и
    `/internal/vms/{vm_id}/stand-setup`) — настройка без restore."""

    correlation_id: str = Field(max_length=128, description="Ключ идемпотентности вызывающего.")
    requested_by_department_id: str | None = Field(default=None, max_length=64)
    test_username: str = Field(default="u", max_length=32, description="Для run_as=test_user.")
    stand_setup: StandSetupSpec
    provisioning: ProvisioningSpec | None = None


class StandSetupAcceptedResponse(BaseModel):
    stand_setup_request_id: str
    status: PrepareForTestStatus


class StandSetupCallbackRequest(BaseModel):
    """Тело POST /internal/servers/{server_id}/stand-setup-done
    (и `/internal/vms/{vm_id}/stand-setup-done`) от воркера."""

    stand_setup_request_id: str = Field(max_length=64)
    succeeded: bool
    failed_step: StandSetupStep | None = None
    error: str | None = Field(default=None, max_length=2048)


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
    server_id: str | None = Field(default=None, description="Физический стенд; None у ВМ-стенда.")
    vm_id: str | None = Field(default=None, description="ВМ-стенд; None у физического.")
    vm_snapshot_name: str | None = Field(default=None, description="Снимок ВМ, на который откатывали.")
    correlation_id: str
    os_version_id: str
    kernel: str
    mode: PrepareForTestMode
    preparation: PrepareForTestPreparation = "full"
    skip_pam_fix: bool = False
    test_username: str
    test_account_credential_id: str | None = None
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
