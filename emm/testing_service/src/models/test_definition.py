"""Каталог тестов — карточка одного теста, конструируемого через слоты команд.

`test_definitions` сама по себе не хранит команду — команда собирается из
упорядоченного списка `test_command_args` (см. `models/test_command_arg.py`).
Карточка держит только паспортные данные теста: чем он называется, к какому
отделу и стенду привязан, готов ли к использованию.

`pinned_stand_id` — сырой id без FK: стенды (`test_stands`) появятся отдельным
доменом позже, а привязка теста к стенду нужна уже сейчас (§2.2 плана
миграции). Тот же приём уже используется в server_service для межсервисных
ссылок — целостность такого поля держит application code, не БД.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestDefinition(Base):
    """Одна карточка каталога тестов."""

    __tablename__ = "test_definitions"
    __table_args__ = (
        CheckConstraint("readiness IN ('ready', 'review', 'broken', 'development')", name="ck_test_definitions_readiness"),
        CheckConstraint("mode IN ('orel', 'smolensk')", name="ck_test_definitions_mode"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    readiness: Mapped[str] = mapped_column(String(32), nullable=False, default="development", server_default="development")
    # Режим безопасности Astra (orel/smolensk), под которым тест всегда
    # запускается — фиксируется владельцем теста при заведении в каталог, не
    # выбирается на запуске/в кампании. server_worker переключает стенд на
    # этот режим перед прогоном (prepare-for-test, шаг mode_switch).
    # server_default — легаси-конвенция каталога (import_catalog.allta.yaml):
    # только явно `*.smolensk`/`*_smolensk`-тесты — смоленск, остальное — орёл.
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="orel", server_default="orel")
    # Per-department скоуп теста. Nullable — платформенные/демонстрационные
    # тесты без владельца-отдела допустимы, как и у part прочих каталогов.
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Soft-ref на test_stands.id (появится волной 4). Без FK — своей таблицы
    # стендов ещё нет, но привязка тест↔стенд нужна уже в этой волне.
    pinned_stand_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Компонент ОС, чьё изменение в changelog "затрагивает" этот тест (§1/§7
    # плана миграции — фильтр СТП-прогона по changelog). Используется ТОЛЬКО
    # этим фильтром, больше нигде. Пусто — тест считается затронутым всегда
    # (безопасный дефолт: лучше лишний прогон, чем пропущенный).
    changelog_component: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Позиционный $5 у legacy `starter.sh` — какой флаг `run.py` клонированной
    # ветки передаст конечному скрипту: "kernel"/"balance"/"oom" или пусто
    # (generic `run.py -n <файл>` без доп. флага). Не enum на уровне БД —
    # просто строка, значение диктует сам `starter.sh` (см. import_catalog).
    starter_suffix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Переопределение таймаута SSH-исполнения для этого теста. NULL — берётся
    # дефолт testing_worker'а (`Settings.ssh_command_timeout_seconds`, сейчас
    # час) — общий cap не для каждого теста одинаково уместен: быстрый smoke
    # не должен час висеть на зависшем стенде, а долгий бенчмарк наоборот
    # может не уложиться в общий дефолт.
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Soft-FK на auth_service identity (`usr_<hex>`/`bot_<hex>`).
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
