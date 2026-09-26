"""Pydantic-схемы для /department-test-settings.

Read — GET по department_id, всегда отдаёт что-то (дефолты, если строки нет).
Write — PUT, upsert; `department_id` берётся из пути, не из тела.

`campaign_sort_rule` — порядок постановки состава кампании в
очередь стендов. Ключи — белый список `CampaignSortKey`: это механизм (какие
поля записи кампании вообще можно сравнивать), а не правило; само правило —
данные отдела, сид миграцией по легаси `allta_back.py:393`.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.constants import VerdictWithoutZephyrRun

CampaignSortKey = Literal["mode", "kernel", "test_case_name", "test_code", "priority"]
CampaignSortDirection = Literal["asc", "desc"]


class CampaignSortRuleItem(BaseModel):
    """Один ключ правила сортировки кампании."""

    model_config = ConfigDict(extra="forbid")

    key: CampaignSortKey = Field(
        description=(
            "Поле записи кампании: `mode` — режим безопасности теста, `kernel` — ядро, "
            "`test_case_name` — имя тест-кейса СТП (Zephyr), если его нет — название теста, "
            "`test_code` — код теста, `priority` — приоритет теста из каталога."
        ),
    )
    direction: CampaignSortDirection = Field(default="asc", description="Направление: `asc` или `desc`.")


# Критерий доступности HTTP-пробы preflight (CONTRACTS.md C3):
# `200` — строго 200 после редиректов, как `requests.get(...).status_code == 200`
#   в легаси (`emm/allta_app_full/libs/liballta.py:1794-1797,1810`);
# `lt500` — любой ответ со статусом < 500 (хост отвечает, пусть и 3xx/401/403).
PreflightOkStatus = Literal["200", "lt500"]


class PreflightHttpProbe(BaseModel):
    """Одна HTTP-проба: какой URL спросить и какой ответ считать «доступен»."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1, max_length=2048, pattern=r"^https?://\S+$",
        description="Полный URL (http/https), который должен ответить перед запуском теста.",
    )
    ok_status: PreflightOkStatus = Field(
        default="200",
        description="`200` — строго 200 (паритет с легаси), `lt500` — любой ответ со статусом < 500.",
    )


def _legacy_http_probes() -> list[PreflightHttpProbe]:
    # `allta_image_conf.py:81-84` (JIRA_URL/CONFLUENCE_URL/GIT_URL/RELEASES_URL),
    # опрашивались как `https://{host}` в `liballta.py:1794-1797`.
    return [
        PreflightHttpProbe(url=f"https://{host}", ok_status="200")
        for host in (
            "jira.astralinux.ru",
            "life.astralinux.ru",
            "git.astralinux.ru",
            "releases.devos.astralinux.ru",
        )
    ]


class PreflightSettings(BaseModel):
    """Проверка внешних сервисов перед запуском теста (T4, CONTRACTS.md C3).

    Хранится в `department_test_settings.preflight` (JSON) и в том же виде
    уезжает воркеру в `QueueClaimItem.preflight`. Дефолты полей — легаси
    `available_astra_services_checker` (`liballta.py:1805-1836`): 4 URL
    строго на 200, 3 DNS (достаточно одного), опрос раз в 180 с, до 120 минут.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Проверять ли внешние сервисы перед запуском.")
    http: list[PreflightHttpProbe] = Field(
        default_factory=_legacy_http_probes, max_length=32,
        description="HTTP-пробы. Пустой список отключает HTTP-часть проверки.",
    )
    dns_hosts: list[str] = Field(
        # `allta_image_conf.py:91` ASTRA_DNS; достаточно любого одного — `liballta.py:1790`.
        default_factory=lambda: ["10.177.128.198", "10.177.180.246", "10.177.181.142"],
        max_length=32,
        description="DNS-серверы; достаточно, чтобы ответил любой один. Пустой список отключает DNS-часть.",
    )
    dns_port: int = Field(
        default=53, ge=1, le=65535,
        description="TCP-порт проверки DNS (легаси слало ICMP-ping, контейнеру он недоступен).",
    )
    poll_interval_seconds: int = Field(
        # `liballta.py:1807` requests_frequency = 180
        default=180, ge=1, le=86400,
        description="Пауза между повторами, пока хоть один сервис недоступен.",
    )
    timeout_seconds: int = Field(
        # `liballta.py:1806` wait_time = 120 (минут)
        default=7200, ge=0, le=7 * 86400,
        description="Сколько всего ждать, прежде чем провалить item.",
    )
    probe_timeout_seconds: int = Field(
        # В легаси таймаута у `requests.get` не было вовсе; 15 с — прежний
        # env-дефолт воркера (`PREFLIGHT_PROBE_TIMEOUT_SECONDS`).
        default=15, ge=1, le=600,
        description="Таймаут одной пробы (один HTTP-запрос или один TCP-коннект к DNS).",
    )

    @field_validator("dns_hosts")
    @classmethod
    def _strip_hosts(cls, value: list[str]) -> list[str]:
        hosts = [host.strip() for host in value]
        for host in hosts:
            if not host or len(host) > 253 or any(ch.isspace() for ch in host):
                raise ValueError(f"invalid DNS host: {host!r}")
        return hosts


class DepartmentTestSettingsUpdate(BaseModel):
    """Тело PUT /department-test-settings/{department_id}. Upsert — все поля опциональны,
    заданные заменяют текущее значение (или дефолт, если строки ещё не было)."""

    retry_enabled: bool | None = Field(default=None, description="Ретраить ли провалившийся прогон один раз.")
    test_username: str | None = Field(
        default=None, min_length=1, max_length=32,
        description=(
            "Подсказка логина учётки исполнения теста. Устарело после: "
            "логин, пароль и ключ задаются на `/department-test-account`."
        ),
    )
    activity_report_auto_generate: bool | None = Field(
        default=None,
        description=(
            "Включить фоновую ежемесячную генерацию HR-отчёта за предыдущий месяц "
            "(1 числа каждого месяца)."
        ),
    )
    campaign_sort_rule: list[CampaignSortRuleItem] | None = Field(
        default=None, min_length=1, max_length=5,
        description=(
            "Порядок постановки состава прогона РЦ в очередь стенда: ключи по убыванию "
            "значимости, каждый не больше одного раза. Одиночные и debug-запуски "
            "(`POST /queue-items`, retry) правилу не подчиняются — FIFO."
        ),
    )

    @field_validator("campaign_sort_rule")
    @classmethod
    def _check_rule(cls, value: list[CampaignSortRuleItem] | None) -> list[CampaignSortRuleItem]:
        if value is None:
            raise ValueError("campaign_sort_rule cannot be null")
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("campaign_sort_rule keys must be unique")
        return value

    preflight: PreflightSettings | None = Field(
        default=None,
        description=(
            "Проверка внешних сервисов перед запуском теста. Заменяет объект "
            "целиком (не мерж по полям)."
        ),
    )
    zephyr_verdict_wait_seconds: int | None = Field(
        default=None, ge=0, le=86400,
        description="Сколько ждать итогового статуса теста в Zephyr после конца SSH-сессии (сек).",
    )
    zephyr_verdict_poll_seconds: int | None = Field(
        default=None, ge=5, le=3600,
        description="Как часто опрашивать Zephyr, пока статус не финальный (сек).",
    )
    zephyr_verdict_unfinished_outcome: Literal["passed", "failed"] | None = Field(
        default=None,
        description="Итог, если за время ожидания тест так и не выставил финальный статус.",
    )
    verdict_without_zephyr_run: VerdictWithoutZephyrRun | None = Field(
        default=None,
        description=(
            "Исход запуска без прогона в Zephyr (debug): unknown — «результат не "
            "определён»; exit_code — по коду выхода starter.sh."
        ),
    )

    log_chunk_interval_seconds: float | None = Field(
        default=None, ge=0.2, le=60,
        description="Как часто воркер шлёт накопленный вывод теста в живой лог (сек).",
    )
    log_chunk_max_bytes: int | None = Field(
        default=None, ge=256, le=1_048_576,
        description="Наибольший кусок живого лога за раз (байт).",
    )

    @field_validator(
        "zephyr_verdict_wait_seconds", "zephyr_verdict_poll_seconds",
        "zephyr_verdict_unfinished_outcome", "verdict_without_zephyr_run",
        "log_chunk_interval_seconds", "log_chunk_max_bytes",
    )
    @classmethod
    def _not_null(cls, value):
        if value is None:
            raise ValueError("cannot be null")
        return value


class DepartmentTestSettingsResponse(BaseModel):
    """Карточка настроек отдела. Если строки в БД нет — отдаётся с дефолтами и `id=None`."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = Field(default=None, description="None, если строка ещё не создана (дефолты).")
    department_id: str = Field(description="Отдел, к которому относятся настройки.")
    retry_enabled: bool = Field(description="Ретраить ли провалившийся прогон один раз.")
    test_username: str = Field(
        description=(
            "Подсказка логина учётки исполнения теста (зеркало логина тестовой "
            "учётки после её настройки, см. `/department-test-account`)."
        ),
    )
    test_account_credential_id: str | None = Field(
        default=None,
        description="Ссылка на тестовую учётку отдела в secret_service (только чтение; задаётся через `/department-test-account`).",
    )
    activity_report_auto_generate: bool = Field(
        description="Фоновая ежемесячная генерация HR-отчёта за предыдущий месяц включена.",
    )
    campaign_sort_rule: list[CampaignSortRuleItem] = Field(
        description="Порядок постановки состава прогона РЦ в очередь стенда.",
    )
    preflight: PreflightSettings = Field(
        default_factory=PreflightSettings,
        description="Проверка внешних сервисов перед запуском теста (легаси-дефолты, если не задано).",
    )
    zephyr_verdict_wait_seconds: int = Field(
        default=2100, description="Сколько ждать итогового статуса теста в Zephyr после конца SSH-сессии (сек).",
    )
    zephyr_verdict_poll_seconds: int = Field(
        default=60, description="Как часто опрашивать Zephyr, пока статус не финальный (сек).",
    )
    zephyr_verdict_unfinished_outcome: Literal["passed", "failed"] = Field(
        default="failed", description="Итог, если тест не выставил финальный статус за время ожидания.",
    )
    verdict_without_zephyr_run: VerdictWithoutZephyrRun = Field(
        default=VerdictWithoutZephyrRun.UNKNOWN,
        description="Исход запуска без прогона в Zephyr: unknown | exit_code.",
    )
    log_chunk_interval_seconds: float = Field(
        default=2.5, description="Как часто воркер шлёт вывод теста в живой лог (сек).",
    )
    log_chunk_max_bytes: int = Field(default=4096, description="Наибольший кусок живого лога (байт).")
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)

    @field_validator("preflight", mode="before")
    @classmethod
    def _preflight_defaults(cls, value):
        # NULL в строке (или отсутствие строки) — легаси-дефолты.
        return PreflightSettings() if value is None else value
