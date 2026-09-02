"""Pydantic-схемы настроек проб статуса (`/settings/probes`).

Платформенный singleton под `account_admin`. Описывает частоту и вкл/выкл двух
групп проб, которые снимает server_worker (reachability = ping+ssh,
power = ipmi/domstate).

Нижние границы интервалов проверяются на уровне полей (`ge`). Правило
«power >= reachability» здесь не выражается (PUT частичный — второе поле может
не приходить), его проверяет сервис на слитых эффективных значениях.
"""

from pydantic import BaseModel, Field

from src.models.probe_settings import (
    MIN_POWER_INTERVAL_SECONDS,
    MIN_REACHABILITY_INTERVAL_SECONDS,
)


class ProbeSettingsResponse(BaseModel):
    """Текущие настройки проб статуса."""

    reachability_probe_interval_seconds: int = Field(
        description="Интервал пробы доступности (ping+ssh), секунды.",
    )
    power_probe_interval_seconds: int = Field(
        description="Интервал пробы питания (ipmi/domstate), секунды.",
    )
    reachability_probe_enabled: bool = Field(
        description="Включена ли проба доступности.",
    )
    power_probe_enabled: bool = Field(
        description="Включена ли проба питания.",
    )


class ProbeSettingsUpdate(BaseModel):
    """Тело PUT — частичное обновление настроек проб.

    Любое поле можно опустить — тогда текущее значение сохраняется. Нижние
    границы интервалов enforce'ятся здесь; порядок (power >= reachability) —
    в сервисе на эффективных значениях после слияния с текущими.
    """

    reachability_probe_interval_seconds: int | None = Field(
        default=None,
        ge=MIN_REACHABILITY_INTERVAL_SECONDS,
        description=(
            "Интервал пробы доступности (ping+ssh), секунды. "
            f"Минимум {MIN_REACHABILITY_INTERVAL_SECONDS}."
        ),
    )
    power_probe_interval_seconds: int | None = Field(
        default=None,
        ge=MIN_POWER_INTERVAL_SECONDS,
        description=(
            "Интервал пробы питания (ipmi/domstate), секунды. "
            f"Минимум {MIN_POWER_INTERVAL_SECONDS}."
        ),
    )
    reachability_probe_enabled: bool | None = Field(
        default=None,
        description="Включить/выключить пробу доступности.",
    )
    power_probe_enabled: bool | None = Field(
        default=None,
        description="Включить/выключить пробу питания.",
    )
