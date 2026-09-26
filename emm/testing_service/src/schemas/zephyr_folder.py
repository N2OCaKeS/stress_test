"""Pydantic-схемы папки Zephyr отдела для версии ОС."""

from datetime import datetime

from pydantic import BaseModel, Field


class ZephyrFolderError(BaseModel):
    """Почему id папки не получен (не найдена и не создана, Zephyr недоступен, шаблон не резолвится)."""

    error_code: str
    message: str


class ZephyrFolderResponse(BaseModel):
    """Папка Zephyr пары (отдел, версия ОС). Нет записи — `id: null`, путь по шаблону отдела."""

    id: str | None = Field(default=None, description="None, если запись ещё не создана.")
    department_id: str
    os_version_id: str
    folder_path: str | None = Field(default=None, description="Путь папки Zephyr (`/stress_test/1.8.5/1.8.5.46`).")
    folder_tree_id: str | None = Field(default=None, description="id папки (`folderTreeId`, легаси `-fti`).")
    is_manual: bool = Field(default=False, description="Задан вручную — генерация СТП его не перезаписывает.")
    resolved_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None
    error: ZephyrFolderError | None = Field(default=None, description="Последняя ошибка поиска, если id не получен.")


class ZephyrFolderManualUpdate(BaseModel):
    """Тело PUT /stp/zephyr-folder — задать id папки вручную."""

    os_version_id: str = Field(..., min_length=1, max_length=64)
    department_id: str | None = Field(default=None, description="По умолчанию — отдел пользователя.")
    folder_tree_id: str = Field(..., min_length=1, max_length=64, description="id папки Zephyr.")
    folder_path: str | None = Field(
        default=None, max_length=512,
        description="Путь папки. Пусто — прежний путь записи или путь по шаблону отдела.",
    )


class ZephyrFolderRefreshRequest(BaseModel):
    """Тело POST /stp/zephyr-folder/refresh — «найти заново»."""

    os_version_id: str = Field(..., min_length=1, max_length=64)
    department_id: str | None = Field(default=None, description="По умолчанию — отдел пользователя.")
