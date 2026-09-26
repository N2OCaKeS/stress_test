"""ВМ-стенды: проверка снимка при постановке и сопоставление для UI.

Снимок ВМ выбирает server_service тем же правилом, что ACS-снимок (T7):
список снимков → имя по шаблонам (`/settings/vm-test` server_service) →
версия сравнивается после нормализации. testing_service нормализацию не
дублирует: server_service отдаёт у каждого снимка `normalized_version`, а
имя версии из каталога (`os_versions.name`) уже нормализовано.

Проверка при постановке — аналог `queue._check_acs_snapshot_available`:
нет снимка нужной версии (и режима, если шаблон с `{mode}`) — 422
`VM_SNAPSHOT_NOT_FOUND` сразу, а не через `failed_step=vm_revert` при
подготовке. Канал недоступен — пропускаем с warning'ом (отказ всё равно
придёт с шага `vm_revert`).
"""

from __future__ import annotations

import logging

from src.core.exceptions import AppException, DomainValidationError
from src.services import server_client

logger = logging.getLogger(__name__)


def snapshot_matches(item: dict, version_name: str, mode: str | None) -> bool:
    """Подходит ли снимок из списка server_service под версию и режим запуска."""
    versions = {item.get("normalized_version"), item.get("version_name")}
    if version_name not in versions:
        return False
    snapshot_mode = item.get("mode")
    return snapshot_mode is None or not mode or snapshot_mode == mode


async def check_vm_snapshot_available(stand, ctx: dict[str, str]) -> None:
    """Отказать в постановке на ВМ-стенд без снимка выбранной РЦ."""
    if not server_client.is_internal_channel_configured():
        return
    rc = ctx.get("RC", "")
    mode = ctx.get("MODE") or None
    try:
        version_name = await server_client.resolve_os_version_name(rc)
        body = await server_client.list_vm_test_snapshots(stand.vm_id)
    except AppException as exc:
        logger.warning("vm snapshot preflight skipped for stand %s / rc %s: %s", stand.id, rc, exc)
        return
    snapshots = body.get("snapshots") or []
    if any(snapshot_matches(item, version_name, mode) for item in snapshots):
        return
    templates = body.get("templates") or []
    raise DomainValidationError(
        error_code="VM_SNAPSHOT_NOT_FOUND",
        message=(
            f"У ВМ этого стенда нет снимка версии «{version_name}»"
            + (f" (режим {mode})" if mode else "")
            + " — ВМ не сможет откатиться на неё при подготовке к тесту. Снимите "
            f"снимок с именем по шаблону ({', '.join(templates) or '—'}) перед запуском."
        ),
        details={
            "stand_id": stand.id, "vm_id": stand.vm_id, "os_version_id": rc,
            "version_name": version_name, "mode": mode, "templates": templates,
            "snapshots": [item.get("name") for item in snapshots],
        },
    )


async def snapshot_mapping(stand) -> dict:
    """Сопоставление «снимок ↔ версия ОС» ВМ-стенда для UI (живой список)."""
    body = await server_client.list_vm_test_snapshots(stand.vm_id, refresh=True)
    return {
        "stand_id": stand.id,
        "vm_id": stand.vm_id,
        "templates": body.get("templates") or [],
        "snapshots": body.get("snapshots") or [],
    }
