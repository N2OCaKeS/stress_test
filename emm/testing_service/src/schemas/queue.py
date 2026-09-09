"""Pydantic-схемы очереди (§2.4, §5 плана миграции) — internal-контракты.

Три разных канала, три разных схемы тела:

* `PrepareForTestCompletedCallback` — входящий callback `server_service` о
  завершении `prepare-for-test` (контракт зафиксирован его же кодом,
  `server_service/src/services/prepare_for_test.py::_deliver_callback`, менять
  нельзя в одностороннем порядке).
* `QueueClaimResponse`/`QueueClaimItem` — ответ `testing_worker`'у на
  `POST /internal/queue/claim`: всё необходимое для одной SSH-сессии одним
  ответом (нет второго раунда за кредами/командой).
* `QueueCompletedRequest` — тело `POST /internal/queue/{id}/completed` от
  `testing_worker`, факт исхода без полного лога (потоковые логи — волна 6).
"""

from pydantic import BaseModel, Field


class PrepareForTestCompletedCallback(BaseModel):
    """Тело POST /internal/prepare-for-test/{prepare_request_id}/completed.

    Отправитель — `server_service`, схема зеркалит его исходящее тело 1:1
    (см. module docstring). `correlation_id` дублирует `queue_items.id` из
    сегмента пути запроса, которым он был инициирован (`start_prepare_for_test`
    передавал его как `correlation_id`).
    """

    correlation_id: str = Field(max_length=128)
    succeeded: bool
    test_username: str | None = Field(default=None, max_length=64)
    test_password: str | None = Field(default=None)
    test_ssh_private_key: str | None = Field(default=None)
    warning: str | None = Field(default=None, max_length=2048)
    failed_step: str | None = Field(default=None, max_length=32)
    error: str | None = Field(default=None, max_length=2048)


class QueueClaimItem(BaseModel):
    """Одно задание для `testing_worker` — всё нужное для SSH-исполнения теста."""

    queue_item_id: str
    host: str = Field(description="IP стенда (см. `server_client.get_connection_info`).")
    test_username: str
    test_password: str
    test_ssh_private_key: str
    command: list[str] = Field(description="Аргументы команды, уже резолвленные (`resolve_command`).")
    command_masked: list[str] = Field(
        description=(
            "Та же команда, но variable-слоты с `is_sensitive=true` заменены "
            "на `***` (`resolve_command_masked`). Единственная версия, которую "
            "воркеру можно класть в лог — сырых кредов в командной строке лога "
            "быть не должно."
        ),
    )
    debug_mode: bool
    is_retry: bool


class QueueClaimResponse(BaseModel):
    """Ответ POST /internal/queue/claim. `item=None` — очередь пуста."""

    item: QueueClaimItem | None = None


class QueueCompletedRequest(BaseModel):
    """Тело POST /internal/queue/{queue_item_id}/completed."""

    succeeded: bool
    exit_code: int | None = Field(default=None)
    error: str | None = Field(default=None, max_length=2048)
