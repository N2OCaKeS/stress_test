"""Асинхронный пайплайн «подготовить сервер под тест» и его тестовые креды.

Две таблицы, обе обслуживают один контракт `POST /internal/servers/{id}/
prepare-for-test` (вызывает `testing_service`, ответ 202 + callback):

* `server_prepare_for_test_requests` — состояние самого запроса. Пайплайн
  идёт часами (restore диска через ACS + авто-prepare + провижн тестового
  пользователя + смена ядра + ребут), поэтому запрос обязан пережить
  рестарт server_service и оставаться читаемым, даже если потребитель
  callback'а недоступен. `correlation_id` уникален — повторный вызов с тем
  же id возвращает уже существующий запрос, а не заводит второй пайплайн.
* `server_test_credentials` — учётка **исполнения теста** на стенде (не
  платформенная `dbos` и не bootstrap-пароль версии ОС). Одна строка на
  сервер, перевыпускается на каждую подготовку: restore всё равно стирает
  предыдущего пользователя. Пароль и приватный ключ лежат тем же
  AES-256-GCM конвертом, что и остальные секреты сервиса
  (`services/secrets_service.py`), под своими AAD.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

# Статусы запроса. Терминальные — `succeeded`/`failed`; ровно один
# `in_progress` на сервер (частичный уникальный индекс ниже).
PREPARE_FOR_TEST_IN_PROGRESS = "in_progress"
PREPARE_FOR_TEST_SUCCEEDED = "succeeded"
PREPARE_FOR_TEST_FAILED = "failed"

PREPARE_FOR_TEST_STATUSES = (
    PREPARE_FOR_TEST_IN_PROGRESS,
    PREPARE_FOR_TEST_SUCCEEDED,
    PREPARE_FOR_TEST_FAILED,
)

# Шаги пайплайна. Значения зафиксированы контрактом callback'а
# (`failed_step`), менять их нельзя без согласования с testing_service.
STEP_RESTORE = "restore"
STEP_PREPARE = "prepare"
STEP_USER_PROVISION = "user_provision"
STEP_KERNEL_CHANGE = "kernel_change"
STEP_MODE_SWITCH = "mode_switch"
STEP_REBOOT_VERIFY = "reboot_verify"

PREPARE_FOR_TEST_STEPS = (
    STEP_RESTORE,
    STEP_PREPARE,
    STEP_USER_PROVISION,
    STEP_KERNEL_CHANGE,
    STEP_MODE_SWITCH,
    STEP_REBOOT_VERIFY,
)

# Режим безопасности Astra, выставляемый пайплайном между сменой ядра и
# финальным ребутом (`astra-modeswitch`). Воронеж (уровень 1) легаси в этом
# пайплайне не использовал — заводим только два значения, третье добавим,
# когда действительно понадобится.
MODE_OREL = "orel"
MODE_SMOLENSK = "smolensk"

PREPARE_FOR_TEST_MODES = (MODE_OREL, MODE_SMOLENSK)


class ServerPrepareForTestRequest(Base):
    """Один запрос `prepare-for-test` со стороны testing_service."""

    __tablename__ = "server_prepare_for_test_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # id очереди/прогона на стороне testing_service. Уникален — на нём держится
    # идемпотентность: повтор того же вызова не плодит второй пайплайн.
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Без FK на os_versions: удаление версии каталога не должно ронять историю
    # запросов, а сама версия к моменту разбора инцидента может уже уехать.
    os_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kernel: Mapped[str] = mapped_column(String(128), nullable=False)
    # Режим безопасности Astra, выставляемый воркером между сменой ядра и
    # финальным ребутом (`astra-modeswitch`). См. `PREPARE_FOR_TEST_MODES`.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    test_username: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_by_department_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Имя сервиса-инициатора из провалидированного X-Service-Identity, не из
    # тела запроса.
    requested_by_service: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PREPARE_FOR_TEST_IN_PROGRESS
    )
    # Шаг, на котором пайплайн находится сейчас (или на котором остановился).
    stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STEP_RESTORE
    )
    failed_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # True, если бронь стенда взял сам этот запрос (сервер был свободен). Тогда
    # на провале мы её и снимаем. Если бронь уже держал testing_service
    # (`acquire-for-service`), трогать её нельзя — он сам решает, когда
    # отпустить стенд.
    reservation_acquired: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    restore_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prepare_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provision_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Доставка callback'а в testing_service. Потребителя может ещё не быть —
    # тогда счётчик и последняя ошибка остаются единственным следом того,
    # почему результат «не доехал».
    callback_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    callback_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    callback_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "correlation_id", name="uq_prepare_for_test_correlation_id"
        ),
        CheckConstraint(
            "status IN ('in_progress', 'succeeded', 'failed')",
            name="ck_prepare_for_test_status",
        ),
        CheckConstraint(
            "failed_step IS NULL OR failed_step IN "
            "('restore', 'prepare', 'user_provision', 'kernel_change', "
            "'mode_switch', 'reboot_verify')",
            name="ck_prepare_for_test_failed_step",
        ),
        CheckConstraint(
            "mode IN ('orel', 'smolensk')",
            name="ck_prepare_for_test_mode",
        ),
        # Один активный пайплайн на сервер. Второй запрос по тому же стенду
        # (с другим correlation_id) отбивается 409 ещё в сервисном слое, но
        # инвариант держим и на БД — гонка двух параллельных вызовов иначе
        # запустила бы два restore на один диск.
        Index(
            "uq_prepare_for_test_active_server",
            "server_id",
            unique=True,
            postgresql_where=(status == PREPARE_FOR_TEST_IN_PROGRESS),
        ),
    )


class ServerTestCredentials(Base):
    """Учётка исполнения теста на сервере (пароль + Ed25519-ключ)."""

    __tablename__ = "server_test_credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False,
    )
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    # Envelope AES-256-GCM (`v<key_ver>$<b64-nonce>$<b64-ct+tag>`), тот же
    # формат и тот же length-cap, что у server_accounts.
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    ssh_public_key: Mapped[str] = mapped_column(Text, nullable=False)
    ssh_private_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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

    __table_args__ = (
        UniqueConstraint("server_id", name="uq_server_test_credentials_server"),
        CheckConstraint(
            "length(password_encrypted) < 8192",
            name="ck_server_test_credentials_password_len",
        ),
        CheckConstraint(
            "length(ssh_private_key_encrypted) < 8192",
            name="ck_server_test_credentials_ssh_key_len",
        ),
    )
