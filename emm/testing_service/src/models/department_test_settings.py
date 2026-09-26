"""Настройки тестирования отдела (§2.4 плана миграции).

Одна строка на `department_id`. Отсутствие строки — штатный случай, а не
ошибка: сервис отдаёт дефолты (см. `services/department_test_settings.py`),
строка появляется только при первом `PUT`.

`activity_report_auto_generate` — включает фоновую ежемесячную генерацию
HR-отчёта за предыдущий месяц (см. `services/activity_report.py::run_auto_generate_tick`,
цикл в `src/main.py`).

`campaign_sort_rule` — порядок, в котором кампания
(`services/test_run.py::create_test_run`) ставит свой состав в очередь
стендов: упорядоченный список `{"key", "direction"}` из белого списка ключей
(`schemas/department_test_settings.py::CampaignSortKey`). Сид миграцией —
легаси `allta_back.py:393` (режим → ядро → имя тест-кейса). Одиночные и
debug-постановки (`POST /queue-items`, retry) правилу не подчиняются — FIFO.

`preflight` — проверка внешних сервисов перед запуском теста:
форма `schemas/department_test_settings.py::PreflightSettings`, уезжает
воркеру в claim payload. `NULL` — легаси-дефолты (см. миграцию
`tp19_department_preflight`).

`zephyr_verdict_*`, `verdict_without_zephyr_run` — ожидание вердикта из
Zephyr, см. `services/queue.py:poll_awaiting_verdicts`.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# Легаси-правило `allta_back.py:393` — тот же литерал, что сидит миграция
# `d5c2e8a41f93_campaign_sort_rule_and_priority`.
LEGACY_CAMPAIGN_SORT_RULE_SQL = (
    """'[{"key": "mode", "direction": "asc"}, {"key": "kernel", "direction": "asc"}, """
    """{"key": "test_case_name", "direction": "asc"}]'::jsonb"""
)

from src.db.base import Base


class DepartmentTestSettings(Base):
    """Per-department переключатели retry/тестовой учётки/HR-отчёта."""

    __tablename__ = "department_test_settings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    retry_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Подсказка логина тестовой учётки: до настройки credential UI
    # предлагает это имя, после — зеркало логина из credential (его читают
    # места, которым нужен только логин без reveal). Источник истины для
    # prepare-for-test и claim — credential `test_account_credential_id`.
    test_username: Mapped[str] = mapped_column(String(32), nullable=False, default="u")
    # Тестовая учётка отдела: ссылка на credential в
    # secret_service (scope=service, service=test_account) с логином, паролем
    # и SSH-парой. NULL — учётка не настроена, запуск падает
    # `TEST_ACCOUNT_NOT_CONFIGURED`.
    test_account_credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Шаблон домашнего каталога учётки, `{TEST_USER}` — логин. Источник
    # переменной `TEST_HOME` (C1, source=test_account, field=home).
    test_account_home_template: Mapped[str] = mapped_column(
        String(256), nullable=False, default="/home/{TEST_USER}", server_default="/home/{TEST_USER}",
    )
    activity_report_auto_generate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    campaign_sort_rule: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, server_default=text(LEGACY_CAMPAIGN_SORT_RULE_SQL),
    )
    preflight: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Вердикт из Zephyr: сколько ждать итогового статуса после
    # конца SSH-сессии и как часто опрашивать (скрипты публикуют асинхронно,
    # с повторами до 30 минут); что считать итогом, если статус так и не стал
    # финальным; и что делать с исходом запуска без прогона в Zephyr (debug).
    zephyr_verdict_wait_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2100, server_default="2100",
    )
    zephyr_verdict_poll_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60",
    )
    zephyr_verdict_unfinished_outcome: Mapped[str] = mapped_column(
        String(16), nullable=False, default="failed", server_default="failed",
    )
    verdict_without_zephyr_run: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown",
    )
    # Живой лог: как часто и какими кусками воркер шлёт вывод
    # теста в `log-chunk` (раньше — константы `ssh_executor`).
    log_chunk_interval_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, default=2.5, server_default="2.5",
    )
    log_chunk_max_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4096, server_default="4096",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
