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

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class TestDefinition(Base):
    """Одна карточка каталога тестов."""

    __tablename__ = "test_definitions"
    __table_args__ = (
        CheckConstraint("readiness IN ('ready', 'review', 'broken', 'development')", name="ck_test_definitions_readiness"),
        CheckConstraint("mode IN ('orel', 'smolensk')", name="ck_test_definitions_mode"),
        CheckConstraint(
            "dates_quoting IN ('shell', 'legacy', 'raw')", name="ck_test_definitions_dates_quoting",
        ),
        CheckConstraint(
            "verdict_source IN ('zephyr', 'exit_code')", name="ck_test_definitions_verdict_source",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Короткая подпись строки в СТП-матрице (`services/stp_matrix.py`). Полное
    # название теста в матрице занимает всю ширину первой колонки, поэтому
    # легаси держал отдельный словарь сокращений (`testname_columns`):
    # "file system benchmark. EXT4" → "FS_EXT4". Пусто — матрица печатает
    # полное название, и по нему же сортирует строки.
    matrix_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Короткое имя теста (D6) — легаси-ключ словаря `tests`
    # (`allta_app_full/allta_image_conf.py:237-305`: `XFS`, `postgresql-sm`,
    # `auditd-p`…). Его подставляет переменная `TEST_SHORT_NAME` (источник
    # `test_field`, fallback — `full_name`) в `--confluence-new-page`
    # (`backup_image.py:297`: `f'{args.TEST}_…'`). Не то же, что
    # `matrix_label`: у матрицы свой словарь сокращений.
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Экранирование токенов в `dates.conf` (D4, `core.constants.DatesQuoting`):
    # `shell` — `shlex.quote`, `legacy` — двойные кавычки у токенов с
    # пробелом, `raw` — без экранирования.
    dates_quoting: Mapped[str] = mapped_column(
        String(16), nullable=False, default="shell", server_default="shell",
    )
    # Источник исхода теста:
    # `zephyr` — статус, выставленный скриптом в прогоне Zephyr (легаси);
    # `exit_code` — код выхода `starter.sh`.
    verdict_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="zephyr", server_default="zephyr",
    )
    # Шаг настройки стенда и `starter_suffix` с — у шагов теста
    # (`models/test_step.py`); API теста отдаёт и принимает их как значения
    # первого шага: это не колонки, а атрибуты экземпляра, их проставляет
    # `services/test_step.py::attach_first_step` перед ответом API.
    stand_setup = None
    starter_suffix = None
    # Профиль подготовки; NULL — профиль отдела по умолчанию.
    provisioning_profile_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("provisioning_profiles.id", ondelete="SET NULL"), nullable=True,
    )
    # Профиль запуска; NULL — профиль отдела по умолчанию.
    launch_profile_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("launch_profiles.id", ondelete="SET NULL"), nullable=True,
    )
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
    # Soft-ref на test_stands.id (появится позже). Без FK — своей таблицы
    # стендов ещё нет, но привязка тест↔стенд нужна уже сейчас.
    pinned_stand_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Компонент ОС, чьё изменение в changelog "затрагивает" этот тест (§1/§7
    # плана миграции — фильтр СТП-прогона по changelog). Используется ТОЛЬКО
    # этим фильтром, больше нигде. Пусто — тест в changelog-объём НЕ попадает
    # (легаси `tests_list`: тест без компонента не выбирался ни одним
    # changelog'ом); в полный набор (`scope=full`) попадает по-прежнему.
    changelog_component: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Переопределение таймаута SSH-исполнения для этого теста. NULL — берётся
    # дефолт testing_worker'а (`Settings.ssh_command_timeout_seconds`, сейчас
    # час) — общий cap не для каждого теста одинаково уместен: быстрый smoke
    # не должен час висеть на зависшем стенде, а долгий бенчмарк наоборот
    # может не уложиться в общий дефолт.
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Приоритет теста для ключа `priority` правила сортировки кампании отдела
    # (`department_test_settings.campaign_sort_rule`). 0 — легаси
    # приоритетов не знал, дефолтное правило этот ключ не использует.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
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
