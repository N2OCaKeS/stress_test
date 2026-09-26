"""Схемы превью запуска теста: `POST /test-definitions/{id}/launch-preview`.

Превью — то же задание воркеру, что собрал бы claim, для
выбранных стенда, версии ОС, ядра и режима, но без постановки в очередь и
без единой записи в БД. Секреты замаскированы (`***`): содержимое файлов,
команда запуска и значения переменных — те же, что увидел бы лог прогона.
"""

from typing import Any

from pydantic import BaseModel, Field

from src.core.constants import TestMode


class LaunchPreviewRequest(BaseModel):
    """Тело запроса превью: то, что выбирает постановщик одиночного запуска."""

    stand_id: str = Field(min_length=1, max_length=64, description="Стенд, на котором собирается задание.")
    os_version_id: str = Field(
        min_length=1, max_length=64,
        description="Версия ОС (РЦ) — id карточки server_service (`launch_context.RC`).",
    )
    kernel: str = Field(min_length=1, max_length=128, description="Ядро (`launch_context.KERNEL`).")
    mode: TestMode | None = Field(
        default=None, description="Режим (`launch_context.MODE`); пусто — режим самого теста.",
    )
    debug: bool = Field(default=False, description="Debug-запуск: шаблоны с `when: debug` и т.п.")
    step_index: int = Field(
        default=0, ge=0, le=99,
        description="Шаг многоступенчатого теста (с 0); задание собирается для этого шага.",
    )
    testenv: bool = Field(
        default=False,
        description=(
            "Одиночный запуск в режиме testenv (только подготовка): маркер testenv — "
            "значение «вкл.» профиля, на стенд дополнительно уходит файл с командой."
        ),
    )


class LaunchPreviewStep(BaseModel):
    """Шаг многоступенчатого теста, для которого собрано задание."""

    index: int = Field(description="Индекс шага с 0.")
    count: int = Field(description="Всего шагов у теста.")
    name: str = ""
    run_mode: str = Field(description="full — через starter.sh с клонированием; rerun — повторный запуск.")


class LaunchPreviewVariable(BaseModel):
    """Строка таблицы переменных: код → значение → источник."""

    code: str = Field(description="Код переменной.")
    label: str | None = Field(default=None, description="Отображаемое имя (для значений задания — пусто).")
    source: str = Field(
        description=(
            "Источник значения (`source` переменной); `claim` — значение, которое знает только "
            "задание (пути профиля, id item'а); `override` — `override_value` слота команды."
        ),
    )
    value: str = Field(description="Значение; sensitive — `***`.")
    sensitive: bool = Field(description="Значение содержит секрет и замаскировано.")
    slot_position: int | None = Field(
        default=None, description="Для `override` — позиция слота команды (с 0).",
    )


class LaunchPreviewFile(BaseModel):
    """Файл, который воркер запишет на стенд по SFTP."""

    role: str = Field(
        description="Назначение пути в профиле запуска: script, token, dates, testenv_marker, command_file.",
    )
    path: str = Field(description="Путь на стенде.")
    mode: str = Field(description="Права файла (`0755`, `0600`, …).")
    sensitive: bool = Field(description="Файл содержит секрет — содержимое замаскировано.")
    content: str | None = Field(
        default=None, description="Содержимое с маской `***`; `null` — не удалось собрать (см. `errors`).",
    )


class LaunchPreviewProfile(BaseModel):
    """Действующая версия профиля запуска для теста на стенде."""

    profile_id: str
    name: str | None = None
    version_id: str
    version: int


class LaunchPreviewError(BaseModel):
    """Этап сборки задания, который не удался: то же, на чём упал бы claim."""

    stage: str = Field(description="launch_profile | paths | dates | git_token | launch | stand_setup.")
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LaunchPreviewResponse(BaseModel):
    """Итог превью. Непустой `errors` — claim с этими параметрами провалил бы item."""

    test_id: str
    stand_id: str
    launch_context: dict[str, str] = Field(description="`RC`, `KERNEL`, `MODE`, как их сохранил бы постановщик.")
    debug: bool
    testenv: bool
    launch_profile: LaunchPreviewProfile | None = None
    step: LaunchPreviewStep | None = Field(default=None, description="Шаг, для которого собрано задание.")
    variables: list[LaunchPreviewVariable] = Field(
        default_factory=list,
        description="Переменные, которые понадобились заданию, по коду; sensitive — `***`.",
    )
    dates_content_masked: str | None = Field(default=None, description="Строка `dates.conf` с маской секретов.")
    files: list[LaunchPreviewFile] = Field(default_factory=list, description="Файлы для стенда в порядке записи.")
    launch_command_masked: str | None = Field(default=None, description="Команда запуска `starter.sh` с маской.")
    stop_command: str | None = Field(default=None, description="Команда остановки дерева процессов (с маской).")
    use_pty: bool | None = None
    cleanup_globs: list[str] = Field(default_factory=list)
    stand_setup: dict[str, Any] | None = Field(
        default=None,
        description="Шаг настройки стенда в форме C2: отрезолвленный скрипт с маской секретов.",
    )
    provisioning: dict[str, Any] | None = Field(
        default=None, description="Профиль подготовки, который уйдёт в prepare-for-test.",
    )
    errors: list[LaunchPreviewError] = Field(default_factory=list)
