"""Сервисная логика легаси-совместимых маршрутов (§12 плана миграции).

Девять маршрутов `allta_app/allta_front.py`, у которых внешние потребители
(телеграм-бот, `libconfluence.py`, скрипты отдела) продолжают ожидать
конкретный путь и форму ответа. `get-times` в этот список сознательно не
входит — исключён отдельным решением ещё до этой волны.

Отдаются двумя роутерами с общей логикой отсюда:

* `/api/testing/v1/legacy-compat/*` — под авторизацией, JSON-обёртки;
* `/rest/api/*` (`api/legacy_public.py`, D17) — легаси-пути и
  легаси-формат, без авторизации, только из разрешённых подсетей
  (`compat_allowed_networks`). Скрипты на стендах ходят туда по
  `http://allta.devos.astralinux.ru/rest/api/...` после переноса DNS.

Два маршрута данных — `get-repo-path*` и `get-astra-config` — в легаси были
не статикой, а результатом живых продюсеров (`ReleaseToRepo`, `get_aqs_json()`
из `libs/liballta.py`). `ReleaseToRepo` уже перенесён раньше, в
`server_service.os_version_repo_resolver` (§H1 плана) — здесь он только
читается через `server_client`. `get_aqs_json()` портирован в этом модуле
как `astra_qa_stand_client` — тот же git-репозиторий, тот же формат ответа,
просто запрос идёт по требованию, а не на каждый деплой allta_app.

Остальные маршруты (`get-box-config`, `get-testname-columns`, `get-stand`,
`known-bugs`, `annotations`) в легаси не имели продюсера вообще — это были
модульные константы. Тут они так и остались константами
(`legacy_static_data.py`), кроме адреса FTP в `get-box-config`: он берётся
из глобальной переменной `FTP_URL`.

URL интеграций (`get-jira-url`/`get-confluence-url`) в публичном compat
отдаются для отдела, выбранного по IP источника (решение владельца 24.09):
IP → стенд в `test_stands` (IP стенда — из server_service) → его отдел;
незнакомый IP или стенды с этим IP в разных отделах — отдел по умолчанию из
`legacy_compat_settings`. Выбор и его причина пишутся в лог и в аудит
(`legacy_compat.department_resolved`), чтобы ошибку сопоставления было видно.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import Action, EntityType
from src.core.exceptions import AppException, AuthorizationError, ConflictError, NotFoundError
from src.dependencies.auth import Identity
from src.services.stand_target import target_of
from src.models.legacy_compat import CompatAllowedNetwork
from src.repositories import department_integration_settings as integration_repo
from src.repositories import global_variable as variable_repo
from src.repositories import legacy_compat as repo
from src.repositories import test_stand as stand_repo
from src.schemas.legacy_compat import (
    CompatNetworkCreate,
    CompatNetworkUpdate,
    LegacyCompatSettingsUpdate,
)
from src.services import (
    astra_qa_stand_client,
    audit_service,
    department_integration_settings,
    legacy_static_data,
    permissions,
    server_client,
)
from src.utils.ids import compat_network_id

logger = logging.getLogger("testing_service.legacy_compat")

# Переменная с адресом FTP (сид миграции).
FTP_URL_VARIABLE = "FTP_URL"

# Поля `department_integration_settings`, которые отдаёт публичный compat.
INTEGRATION_URL_FIELDS = ("jira_base_url", "confluence_base_url")


async def get_repo_paths() -> dict[str, list[str]]:
    """`{версия: [строки sources.list]}` для всех версий, у которых репозитории уже резолвлены.

    Источник — `os_versions.repositories` в server_service (см.
    `os_version_service.resolve_repositories`, порт легаси `ReleaseToRepo`).
    Версии, для которых репозитории ещё ни разу не резолвили explicit-вызовом
    `resolve-repositories`, в выдачу не попадают — маршрут не триггерит запись
    в чужую БД как побочный эффект read-запроса.
    """
    versions = await server_client.list_os_versions()
    result = {
        str(version["name"]): list(version.get("repositories") or [])
        for version in versions
        if version.get("name") and version.get("repositories")
    }
    return dict(sorted(result.items()))


async def get_astra_config() -> dict:
    """Живой `astra-config.json` из git (см. `astra_qa_stand_client`)."""
    return await astra_qa_stand_client.get_astra_config()


def get_box_config() -> dict:
    """Легаси `box-config.json` как есть — статика, продюсера в легаси не было."""
    return legacy_static_data.BOX_CONFIG


async def box_config(db: AsyncSession) -> dict:
    """`box-config.json` с адресом FTP из переменной `FTP_URL`.

    Нет переменной или пустое значение — легаси-адрес как есть.
    """
    base = await _static_value(db, FTP_URL_VARIABLE)
    if not base:
        return legacy_static_data.BOX_CONFIG
    base = base.rstrip("/")
    legacy = legacy_static_data.LEGACY_FTP_BASE

    def _swap(value: str) -> str:
        return base + value[len(legacy):] if value.startswith(legacy + "/") else value

    return {
        key: [{name: [_swap(item) for item in values] for name, values in entry.items()} for entry in entries]
        for key, entries in legacy_static_data.BOX_CONFIG.items()
    }


async def _static_value(db: AsyncSession, code: str) -> str | None:
    variable = await variable_repo.get_by_code(db, code)
    if variable is None or not isinstance(variable.source_ref, dict):
        return None
    value = variable.source_ref.get("value")
    return str(value).strip() if value is not None else None


def get_testname_columns() -> dict[str, str]:
    return legacy_static_data.TESTNAME_COLUMNS


def get_known_bugs() -> dict[str, dict[str, str]]:
    return legacy_static_data.KNOWN_BUGS


def get_annotations() -> dict[str, str]:
    return legacy_static_data.ANNOTATIONS


def get_stand_description_html() -> str:
    return legacy_static_data.STAND_DESCRIPTION_HTML


async def get_jira_url(db: AsyncSession, identity: Identity, department_id: str) -> str | None:
    """`jira_base_url` отдела — легаси-эквивалент был глобальной строкой без отдела."""
    settings = await department_integration_settings.get_effective_for(db, identity, department_id)
    return settings.get("jira_base_url")


async def get_confluence_url(db: AsyncSession, identity: Identity, department_id: str) -> str | None:
    settings = await department_integration_settings.get_effective_for(db, identity, department_id)
    return settings.get("confluence_base_url")


async def available_kernels_from_rc(rc: str) -> list[str]:
    """Ядра, доступные для версии `rc` (легаси `available-kernels-from-<rc>`).

    Легаси адресовал версию именем сборки (`"1.8.5.46"`), не внутренним
    `os_version_id` каталога — сначала резолвим id по имени, потом переиспользуем
    тот же путь, что и обычный конструктор теста (`server_client.resolve_os_kernels`).
    """
    version = await server_client.find_os_version_by_name(rc)
    if version is None:
        raise NotFoundError(
            error_code="LEGACY_RC_NOT_FOUND",
            message=f"OS version '{rc}' is not present in the catalog",
            details={"rc": rc},
        )
    return await server_client.resolve_os_kernels(str(version["id"]))


# ── Публичный compat: доступ по подсетям ─────────────────────────────


@dataclass
class CompatSource:
    """Источник публичного запроса, прошедший проверку подсети."""

    ip: str
    network_id: str
    cidr: str


def _match_network(networks: list[CompatAllowedNetwork], ip: str) -> CompatAllowedNetwork | None:
    """Самая узкая включённая подсеть, в которую попадает `ip`."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    best: tuple[int, CompatAllowedNetwork] | None = None
    for row in networks:
        try:
            network = ipaddress.ip_network(row.cidr, strict=False)
        except ValueError:
            logger.warning("compat_allowed_networks: битая запись %s (%r) пропущена", row.id, row.cidr)
            continue
        if address.version == network.version and address in network:
            if best is None or network.prefixlen > best[0]:
                best = (network.prefixlen, row)
    return best[1] if best else None


async def check_source(db: AsyncSession, ip: str | None) -> CompatSource:
    """Пропустить запрос из разрешённой подсети, иначе 403 `LEGACY_COMPAT_SOURCE_FORBIDDEN`.

    Отказ отдельно в аудит не пишется: 403 и так уходит в `http.access_denied`
    (`AuditAccessMiddleware`), а IP источника — в поле `actor_ip` события.
    """
    networks = await repo.list_networks(db, enabled_only=True)
    network = _match_network(networks, ip) if ip else None
    if network is None:
        logger.warning("legacy compat: запрос с %s отклонён — адрес вне разрешённых подсетей", ip)
        raise AuthorizationError(
            error_code="LEGACY_COMPAT_SOURCE_FORBIDDEN",
            message="Source address is not in the allowed networks of the legacy compat API",
            details={"ip": ip},
        )
    return CompatSource(ip=str(ip), network_id=network.id, cidr=network.cidr)


# ── Публичный compat: отдел по IP стенда ─────────────────────────────


@dataclass
class _StandIpCache:
    at: float = 0.0
    # IP → [(stand_id, department_id)]
    by_ip: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    lock: asyncio.Lock | None = None


_cache = _StandIpCache()


def reset_stand_ip_cache() -> None:
    """Сбросить карту «IP → стенд» (тесты, смена стендов в UI не ждёт TTL)."""
    global _cache
    _cache = _StandIpCache()


async def _host_of(stand) -> str | None:
    """IP стенда — сервера или гостя ВМ."""
    server_id = stand.server_id or stand.vm_id
    try:
        info = await server_client.get_stand_connection_info(target_of(stand))
    except AppException as exc:
        # Сервер удалён, это ВМ без connection-info, канал не настроен —
        # стенд просто не участвует в сопоставлении.
        logger.warning("legacy compat: нет IP для сервера %s (%s)", server_id, exc.error_code)
        return None
    except Exception as exc:  # noqa: BLE001 — сеть до server_service
        logger.warning("legacy compat: connection-info %s не получен: %s", server_id, exc)
        return None
    host = str(info.get("host") or "").strip()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


async def _stand_ip_map(db: AsyncSession) -> dict[str, list[tuple[str, str]]]:
    """Карта «IP → стенды» с TTL `LEGACY_COMPAT_STAND_IP_CACHE_SECONDS`.

    IP стендов намеренно не хранится в `test_stands` (§4 плана миграции),
    поэтому карта собирается запросами connection-info по всем стендам —
    параллельно и не чаще раза в TTL на процесс.
    """
    ttl = get_settings().legacy_compat_stand_ip_cache_seconds
    cache = _cache
    if cache.at and time.monotonic() - cache.at < ttl:
        return cache.by_ip
    if cache.lock is None:
        cache.lock = asyncio.Lock()
    async with cache.lock:
        if cache.at and time.monotonic() - cache.at < ttl:
            return cache.by_ip
        stands = await stand_repo.list_all(db, limit=10_000)
        hosts = await asyncio.gather(*(_host_of(stand) for stand in stands))
        by_ip: dict[str, list[tuple[str, str]]] = {}
        for stand, host in zip(stands, hosts):
            if host:
                by_ip.setdefault(host, []).append((stand.id, stand.department_id))
        cache.by_ip = by_ip
        cache.at = time.monotonic()
        return by_ip


@dataclass
class DepartmentChoice:
    """Какой отдел выбран для IP и почему."""

    department_id: str | None
    reason: str  # stand | default | ambiguous | no_default
    stand_ids: list[str] = field(default_factory=list)


async def resolve_department(db: AsyncSession, ip: str) -> DepartmentChoice:
    """Отдел для URL интеграций по IP источника (решение владельца 24.09)."""
    stands = (await _stand_ip_map(db)).get(ip, [])
    stand_ids = [stand_id for stand_id, _ in stands]
    departments = {department_id for _, department_id in stands}
    if len(departments) == 1:
        return DepartmentChoice(department_id=departments.pop(), reason="stand", stand_ids=stand_ids)
    settings_row = await repo.get_settings(db)
    default = settings_row.default_department_id if settings_row else None
    if default is None:
        return DepartmentChoice(department_id=None, reason="no_default", stand_ids=stand_ids)
    return DepartmentChoice(
        department_id=default, reason="ambiguous" if stands else "default", stand_ids=stand_ids,
    )


def legacy_host(url: str) -> str:
    """URL в легаси-формате — без схемы и завершающего `/`.

    Легаси отдавал голое имя (`jira.astralinux.ru`, `allta_image_conf.py:81-82`),
    а потребители сами дописывают схему: `f"https://{CONFLUENCE_URL}"`
    (`libs/libconfluence.py:16`), `f'https://{JIRA_URL}'` (`libs/zefir.py:452`).
    В `department_integration_settings` URL хранится со схемой.
    """
    text = url.strip()
    parts = urlsplit(text)
    if parts.scheme and parts.netloc:
        text = parts.netloc + parts.path
    return text.rstrip("/")


async def integration_url_for_source(db: AsyncSession, source: CompatSource, field_name: str, route: str) -> str:
    """URL интеграции отдела, выбранного по IP источника, в легаси-формате."""
    choice = await resolve_department(db, source.ip)
    logger.info(
        "legacy compat: %s с %s → отдел %s (%s, стенды: %s)",
        route, source.ip, choice.department_id, choice.reason, ",".join(choice.stand_ids) or "—",
    )
    audit_service.emit(
        "legacy_compat.department_resolved",
        actor_type="anonymous",
        target_type="department",
        target_id=choice.department_id,
        department_id=choice.department_id,
        status="success" if choice.department_id else "failure",
        allowed=True,
        details={
            "route": route, "source_ip": source.ip, "network": source.cidr,
            "reason": choice.reason, "stand_ids": choice.stand_ids,
        },
    )
    if choice.department_id is None:
        raise NotFoundError(
            error_code="LEGACY_COMPAT_DEPARTMENT_UNKNOWN",
            message=(
                "No stand with this address and no default department in the legacy compat settings"
            ),
            details={"ip": source.ip, "reason": choice.reason},
        )
    row = await integration_repo.get_by_department(db, choice.department_id)
    url = getattr(row, field_name, None) if row is not None else None
    if not url or not str(url).strip():
        raise NotFoundError(
            error_code="LEGACY_COMPAT_URL_NOT_CONFIGURED",
            message=f"'{field_name}' is not set in the department integration settings",
            details={"department_id": choice.department_id, "field": field_name, "reason": choice.reason},
        )
    return legacy_host(str(url))


# ── Настройки compat (UI) ─────────────────────────────────────────────


async def _require(db: AsyncSession, identity: Identity, action: str, audit_action: str | None) -> None:
    try:
        await permissions.require_action(db, identity, EntityType.LEGACY_COMPAT, action)
    except AuthorizationError:
        if audit_action is not None:
            audit_service.emit(
                audit_action, target_type="legacy_compat",
                status="denied", allowed=False, details={"reason": "permission_denied"},
            )
        raise


async def list_networks(db: AsyncSession, identity: Identity) -> list[CompatAllowedNetwork]:
    await _require(db, identity, Action.VIEW, None)
    return await repo.list_networks(db)


async def _network_or_404(db: AsyncSession, network_id: str, audit_action: str) -> CompatAllowedNetwork:
    row = await repo.get_network(db, network_id)
    if row is None:
        audit_service.emit(
            audit_action, target_id=network_id, target_type="compat_allowed_network",
            status="failure", allowed=True, details={"reason": "not_found"},
        )
        raise NotFoundError(error_code="COMPAT_NETWORK_NOT_FOUND", message="Allowed network not found")
    return row


def _duplicate(audit_action: str, cidr: str, target_id: str | None = None) -> ConflictError:
    audit_service.emit(
        audit_action, target_id=target_id, target_type="compat_allowed_network",
        status="failure", allowed=True, details={"reason": "duplicate", "cidr": cidr},
    )
    return ConflictError(
        error_code="COMPAT_NETWORK_DUPLICATE",
        message="This network is already in the list",
        details={"cidr": cidr},
    )


async def create_network(db: AsyncSession, identity: Identity, payload: CompatNetworkCreate) -> CompatAllowedNetwork:
    await _require(db, identity, Action.UPDATE, "compat_network.create")
    data = payload.model_dump()
    data["id"] = compat_network_id()
    data["created_by"] = identity.user_id
    try:
        row = await repo.create_network(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _duplicate("compat_network.create", payload.cidr) from exc
    await db.refresh(row)
    audit_service.emit(
        "compat_network.create", target_id=row.id, target_type="compat_allowed_network",
        status="success", allowed=True,
        details={"cidr": row.cidr, "enabled": row.enabled, "description": row.description},
    )
    return row


async def update_network(
    db: AsyncSession, identity: Identity, network_id: str, payload: CompatNetworkUpdate,
) -> CompatAllowedNetwork:
    await _require(db, identity, Action.UPDATE, "compat_network.update")
    row = await _network_or_404(db, network_id, "compat_network.update")
    raw = payload.model_dump(exclude_unset=True)
    changes = {
        key: value for key, value in raw.items()
        if not (value is None and key in {"cidr", "enabled"}) and getattr(row, key) != value
    }
    if not changes:
        return row
    before = {"cidr": row.cidr, "enabled": row.enabled}
    try:
        await repo.update_network(db, row, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _duplicate("compat_network.update", str(changes.get("cidr")), network_id) from exc
    await db.refresh(row)
    audit_service.emit(
        "compat_network.update", target_id=row.id, target_type="compat_allowed_network",
        status="success", allowed=True,
        details={"fields": sorted(changes), "before": before, "cidr": row.cidr, "enabled": row.enabled},
    )
    return row


async def delete_network(db: AsyncSession, identity: Identity, network_id: str) -> None:
    await _require(db, identity, Action.UPDATE, "compat_network.delete")
    row = await _network_or_404(db, network_id, "compat_network.delete")
    cidr = row.cidr
    await repo.delete_network(db, row)
    await db.commit()
    audit_service.emit(
        "compat_network.delete", target_id=network_id, target_type="compat_allowed_network",
        status="success", allowed=True, details={"cidr": cidr},
    )


async def get_compat_settings(db: AsyncSession, identity: Identity) -> dict:
    await _require(db, identity, Action.VIEW, None)
    row = await repo.get_settings(db)
    if row is None:
        return {"default_department_id": None, "updated_by": None, "updated_at": None}
    return {
        "default_department_id": row.default_department_id,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


async def update_compat_settings(
    db: AsyncSession, identity: Identity, payload: LegacyCompatSettingsUpdate,
) -> dict:
    await _require(db, identity, Action.UPDATE, "legacy_compat_settings.update")
    before = await repo.get_settings(db)
    previous = before.default_department_id if before else None
    row = await repo.upsert_settings(
        db, {"default_department_id": payload.default_department_id, "updated_by": identity.user_id},
    )
    await db.commit()
    await db.refresh(row)
    audit_service.emit(
        "legacy_compat_settings.update", target_type="legacy_compat",
        status="success", allowed=True,
        details={"default_department_id": row.default_department_id, "previous": previous},
    )
    return {
        "default_department_id": row.default_department_id,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


async def explain(db: AsyncSession, identity: Identity, ip: str) -> dict:
    """Что compat сделает с запросом с этого IP — проверка сопоставления из UI."""
    await _require(db, identity, Action.VIEW, None)
    networks = await repo.list_networks(db, enabled_only=True)
    network = _match_network(networks, ip)
    choice = await resolve_department(db, ip)
    return {
        "ip": ip,
        "allowed": network is not None,
        "network_id": network.id if network else None,
        "cidr": network.cidr if network else None,
        "department_id": choice.department_id,
        "reason": choice.reason,
        "stand_ids": choice.stand_ids,
    }
