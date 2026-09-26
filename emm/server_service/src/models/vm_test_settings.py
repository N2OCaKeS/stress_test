"""Настройки подготовки ВМ-стендов под тест — платформенный singleton.

Одна строка на платформу (PK `SINGLETON_ID`). Сейчас одно поле — шаблоны имени
снимка ВМ, на который `prepare-for-test` откатывает ВМ перед тестом (снимок
ищется по имени, отдельной таблицы сопоставления нет).

Почему шаблоны, а не зашитое `{hostname}-{version}`, как у ACS: у снимков ВМ
другая схема имён. Легаси называл снимок ВМ просто версией —
`emm/allta_app_full/allta_conf.json` (`cz_comm.stand1/2/6…`: `"1.7.5.9":
"1.7.5.9"`), откат — `virsh snapshot-revert --snapshotname <версия>`
(`emm/allta_app_full/libs/liballta.py:1503-1508`). VM-домен server_service
снимает эталоны `<ver>_orel` / `<ver>_smolensk` (`server_worker/src/tasks/
vms.py::_build_single`) и `<rc>` после astra-update. Шаблоны перебираются по
порядку, первый подошедший снимок и берётся.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base

SINGLETON_ID = "default"

# Легаси-имя (версия целиком) первым, затем эталон VM-домена по режиму.
DEFAULT_SNAPSHOT_NAME_TEMPLATES = ["{version}", "{version}_{mode}"]


class VmTestSettings(Base):
    """Платформенный singleton-конфиг подготовки ВМ-стендов под тест."""

    __tablename__ = "vm_test_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=SINGLETON_ID)
    # Упорядоченный список шаблонов имени снимка. Плейсхолдеры: `{version}`
    # (обязателен, ровно один раз; сравнивается после нормализации
    # `normalize_os_version_name`), `{mode}` (`orel`/`smolensk` — режим
    # запроса), `{hostname}` (hostname гостя, иначе имя ВМ), `{vm_name}`.
    snapshot_name_templates: Mapped[list] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
