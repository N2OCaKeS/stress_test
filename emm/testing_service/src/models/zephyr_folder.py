"""Папка Zephyr (дерево test-run'ов) отдела для одной версии ОС.

Одна строка на пару `(department_id, os_version_id)`. Скрипты находят свой
прогон в Zephyr по `folderTreeId` (`-fti`), имени прогона и тест-кейсу
(легаси `allta_app_full/libs/zefir.py:156-199`, `finder()`); раньше id папки
брался из словаря `cycle_tree_index`, который наполнял бот
(`allta_app_full/libs/liballta.py:2119-2166`). Теперь id находит (или
создаёт) генерация СТП по пути из шаблона
`department_integration_settings.zephyr_folder_path_template` и сохраняет
здесь, а переменная `FOLDER_TREE_ID` (источник `zephyr_folder`) читает его
отсюда.

`is_manual=true` — id задан руками в UI: автоматика (генерация СТП) такую
строку не перезаписывает, только явное «найти заново».
`folder_tree_id` может быть пустым: путь известен, но Zephyr не отдал id
(папки нет и создать не удалось) — резолв `FOLDER_TREE_ID` тогда падает с
подсказкой.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class ZephyrFolder(Base):
    """`folder_tree_id` папки Zephyr для пары (отдел, версия ОС)."""

    __tablename__ = "zephyr_folders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    os_version_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    folder_path: Mapped[str] = mapped_column(String(512), nullable=False)
    folder_tree_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Когда `folder_tree_id` в последний раз получен из Zephyr или задан руками.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Soft-ref на auth_service identity; `None` — запись завела автоматика.
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("department_id", "os_version_id", name="uq_zephyr_folders_dept_os_version"),
    )
