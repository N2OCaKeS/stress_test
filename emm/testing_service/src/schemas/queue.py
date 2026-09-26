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
  `testing_worker`, факт исхода без полного лога (потоковые логи появятся позже).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.schemas.department_test_settings import PreflightSettings


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


class QueueClaimFile(BaseModel):
    """Один файл задания воркеру."""

    path: str = Field(description="Абсолютный путь на стенде.")
    content: str
    mode: str = Field(default="0644", pattern=r"^0[0-7]{3}$", description="Права файла (восьмеричная строка).")
    sensitive: bool = Field(default=False, description="Содержимое секретно — не логировать.")


class QueueClaimStep(BaseModel):
    """Шаг многоступенчатого теста в задании воркеру."""

    index: int = Field(ge=0, description="Индекс шага с 0.")
    count: int = Field(ge=1, description="Сколько шагов у теста.")
    name: str = Field(default="", description="Подпись шага (для лога).")


class QueueClaimItem(BaseModel):
    """Одно задание для `testing_worker` — всё нужное для SSH-исполнения теста."""

    queue_item_id: str
    host: str = Field(description="IP стенда (см. `server_client.get_connection_info`).")
    test_username: str
    test_ssh_private_key: str
    files: list[QueueClaimFile] = Field(
        description=(
            "Файлы, которые воркер кладёт на стенд по SFTP в этом порядке до "
            "запуска: starter.sh, git-токен, dates, testenv-маркер, "
            "в prepare_only — ещё файл с командой. Пути и содержимое — из профиля "
            "запуска; воркер своих путей не знает."
        ),
    )
    cleanup_globs: list[str] = Field(
        default_factory=list,
        description="Маски файлов, удаляемых на стенде перед записью (T2, опция профиля; по умолчанию пусто).",
    )
    launch_command: str = Field(description="Команда запуска (строка shell, аргументы экранированы).")
    launch_command_masked: str = Field(description="Та же команда для лога: sensitive-значения заменены на ***.")
    stop_command: str = Field(
        description=(
            "Команда остановки (T1): всё дерево потомков starter.sh по PPID, TERM → "
            "grace → KILL, под sudo. Её же воркер шлёт по таймауту и по skip/pause."
        ),
    )
    use_pty: bool = Field(default=True, description="Исполнять в pty (T5): вывод построчно, stdout+stderr одним потоком.")
    redact_values: list[str] = Field(
        default_factory=list,
        description="Значения, которые воркер вырезает из error и из каждого куска живого лога.",
    )
    launch_profile_version_id: str = Field(description="Версия профиля запуска, которой собрано задание.")
    log_chunk_interval_seconds: float = Field(default=2.5, description="Как часто слать куски живого лога (сек).")
    log_chunk_max_bytes: int = Field(default=4096, description="Наибольший кусок живого лога (байт).")
    command_timeout_seconds: int | None = Field(
        default=None,
        description=(
            "`test_definitions.timeout_seconds` этого теста. Пусто — воркер "
            "берёт свой дефолт (`Settings.ssh_command_timeout_seconds`)."
        ),
    )
    debug_mode: bool
    is_retry: bool
    prepare_only: bool = Field(
        default=False,
        description=(
            "Легаси testenv-режим: маркер testenv — `on`, starter.sh готовит "
            "стенд и выходит, не запуская тест; в `files` есть файл с командой, "
            "которой тест был бы запущен. Исход — `prepared`."
        ),
    )
    preflight: PreflightSettings | None = Field(
        default=None,
        description=(
            "Проверка внешних сервисов перед запуском — "
            "`department_test_settings.preflight` отдела стенда на момент claim. "
            "Нет поля — воркер берёт свои env-настройки (фолбэк)."
        ),
    )
    step: QueueClaimStep = Field(
        default_factory=lambda: QueueClaimStep(index=0, count=1),
        description=(
            "Какой шаг теста исполняет задание: у одношагового — `{index: 0, count: 1}`. "
            "Воркер подписывает им сегмент лога; исход шага — обычный `completed`."
        ),
    )


class QueueClaimResponse(BaseModel):
    """Ответ POST /internal/queue/claim. `item=None` — очередь пуста."""

    item: QueueClaimItem | None = None


class QueueCompletedRequest(BaseModel):
    """Тело POST /internal/queue/{queue_item_id}/completed."""

    succeeded: bool
    exit_code: int | None = Field(default=None)
    error: str | None = Field(default=None, max_length=2048)
    timed_out: bool = Field(
        default=False,
        description=(
            "SSH-сессия упёрлась в `command_timeout`, а не в ненулевой код "
            "возврата/обрыв соединения (см. `ssh_executor.py`). Игнорируется "
            "при `succeeded=True`; на провале определяет, уйдёт ли item в "
            "`timed_out` вместо generic `failed`."
        ),
    )
    interrupted: Literal["skip", "pause"] | None = Field(
        default=None,
        description=(
            "Заполняется, когда SSH-сессия была оборвана по заявке оператора "
            "(`interrupt-check` вернул действие). В этом случае "
            "`succeeded`/`exit_code`/`error` игнорируются: исхода у теста нет, "
            "элемент уходит в `skipped` либо `paused`."
        ),
    )


class QueueInterruptCheckResponse(BaseModel):
    """Ответ GET /internal/queue/{queue_item_id}/interrupt-check.

    `action=null` — прерывать нечего, воркер продолжает исполнение. То же
    самое отдаётся для неизвестного `queue_item_id`: у воркера это опрос по
    таймеру, и 404 посреди уже идущего теста для него не более информативен,
    чем «прерывания нет».
    """

    action: Literal["skip", "pause"] | None = None


class QueueItemSummaryResponse(BaseModel):
    """Ответ GET /test-stands/{id}/current-queue-item (§8.6 плана миграции).

    Минимум, которого фронтенду достаточно, чтобы показать кнопку «Живой лог
    теста» в консоли сервера и открыть `WS /queue-items/{id}/log/stream` —
    полную карточку item'а этот эндпоинт не отдаёт.
    """

    queue_item_id: str
    state: str
    test_id: str
    started_at: datetime | None = None
    # Запрошенное, но ещё не отработанное воркером прерывание — карточке стенда
    # этого хватает, чтобы показать «Останавливается…» без второго запроса.
    interrupt_action: str | None = None
    estimated_finish_at: datetime | None = Field(
        default=None,
        description=(
            "Оценка освобождения стенда — `started_at` + таймаут теста, "
            "worst-case, не средняя длительность. `null`, если item ещё не "
            "стартовал или у теста не задан таймаут."
        ),
    )


class QueuePreflightStateRequest(BaseModel):
    """Тело POST /internal/queue/{queue_item_id}/preflight-state.

    `waiting` — воркер ждёт внешние сервисы перед запуском item'а, `ok` —
    дождался (или сдался, или item сняли): ожидание снимается.
    """

    state: Literal["waiting", "ok"]
    unavailable: list[str] = Field(
        default_factory=list, max_length=64,
        description="Недоступные пробы (URL, `dns`), как их называет воркер.",
    )


class PreflightStatusResponse(BaseModel):
    """Ответ GET /preflight/status — ожидание внешних сервисов в отделе пользователя."""

    state: Literal["waiting", "ok"] = Field(description="`waiting` — хоть один тест отдела ждёт внешние сервисы.")
    unavailable: list[str] = Field(default_factory=list, description="Недоступные сервисы (объединение по тестам).")
    since: datetime | None = Field(default=None, description="С какого момента ждём (самое раннее ожидание).")
    waiting_items: int = Field(default=0, description="Сколько тестов ждут.")


class StandSetupCompletedCallback(BaseModel):
    """Тело POST /internal/stand-setup/{id}/completed от server_service."""

    correlation_id: str = Field(max_length=128)
    succeeded: bool
    failed_step: str | None = Field(default=None, max_length=32)
    error: str | None = Field(default=None, max_length=2048)
