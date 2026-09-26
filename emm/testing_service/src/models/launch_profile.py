"""Профиль запуска теста.

Раньше `starter.sh`, пути на стенде, команда остановки и режим терминала
были зашиты в код воркера и `services/queue.py`. Теперь это данные:

* `launch_profiles` — профиль. `department_id IS NULL` — общий профиль по
  умолчанию (сид миграции `tp09_launch_profiles`: легаси `starter.sh` 1:1);
  у отдела может быть свой, `is_default=true` — тот, что берут тесты отдела
  без явного `test_definitions.launch_profile_id`.
* `launch_profile_versions` — иммутабельные версии профиля. Правка в UI —
  новая версия; `launch_profiles.current_version_id` указывает на
  действующую, а `queue_items.launch_profile_version_id` фиксирует, чем
  запускали конкретный item.

Подстановки: в `starter_script` и `stop_command_template` — `{{CODE}}`
(там bash, у которого свои `{...}`/`${...}`), в путях и
`launch_command_template` — `{CODE}` (`services/variable_resolver.py`).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class LaunchProfile(Base):
    """Профиль запуска (общий или отдела)."""

    __tablename__ = "launch_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    current_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class LaunchProfileVersion(Base):
    """Одна версия профиля. Не изменяется после создания."""

    __tablename__ = "launch_profile_versions"
    __table_args__ = (
        UniqueConstraint("profile_id", "version", name="uq_launch_profile_versions_profile_version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("launch_profiles.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Текст `starter.sh` с подстановками `{{CODE}}`.
    starter_script: Mapped[str] = mapped_column(Text, nullable=False)
    # `{"repo_url", "mode": "branch"|"full", "depth": int|null, "credential": "git"}`.
    clone: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Шаблоны путей на стенде: `{"script", "dates", "token", "testenv_marker", "command_file"}`.
    paths: Mapped[dict] = mapped_column(JSONB, nullable=False)
    launch_command_template: Mapped[str] = mapped_column(Text, nullable=False)
    stop_command_template: Mapped[str] = mapped_column(Text, nullable=False)
    stop_grace_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    use_pty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # `{"on_value", "off_value", "cleanup_other": bool}` (T2).
    testenv: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Скрипт повторного запуска: кладётся на
    # стенд вместо `starter.sh` по тому же пути и запускается той же
    # командой — остановка (T1) работает без изменений. `{{CODE}}`, как в
    # `starter_script`. Пусто — шаги `rerun` с этим профилем не запускаются.
    rerun_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Дополнительные файлы на стенде: `[{"path", "content", "mode",
    # "sensitive"}]`, путь — `{CODE}`, содержимое — `{{CODE}}`.
    extra_files: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
