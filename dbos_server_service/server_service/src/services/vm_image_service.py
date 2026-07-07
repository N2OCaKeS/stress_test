"""Каталог боксов-образов ВМ: синк с FTP `test-box-config.json` + резолв box→url.

Каталог — глобальный (не dept-scoped), зеркалит секцию `libvirt_box` конфига
боксов на FTP. `refresh` тянет конфиг, upsert'ит записи; `list` отдаёт каталог.

Главное назначение каталога — резолв `box`→`box_url` при `vm.create`: без URL
воркер не знает, откуда скачивать образ (см. `services/vm.create_vm`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import Action, EntityType, VmImageKind
from src.core.exceptions import AuthorizationError, ServiceUnavailableError
from src.repositories import vm_image as repo
from src.schemas.identity import IdentityContext
from src.services import audit_service, permissions

logger = logging.getLogger("server_service.vm_image_service")

# Секция конфига боксов, где живут libvirt-образы (имя→url или имя→объект).
_LIBVIRT_BOX_SECTION = "libvirt_box"


def _fetch_box_config_from_network() -> dict:
    """Загрузить и распарсить `test-box-config.json` с FTP/HTTP.

    Единственная точка реального сетевого вызова — её подменяют тесты, чтобы не
    ходить на FTP. Сетевые сбои / битый JSON → ServiceUnavailableError.
    """
    settings = get_settings()
    url = settings.vm_box_config_url
    timeout = settings.vm_box_config_timeout_seconds
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — ftp/http каталог образов
            raw = resp.read()
    except (OSError, ValueError) as exc:
        logger.warning("fetch box config failed: %s: %s", type(exc).__name__, exc)
        raise ServiceUnavailableError(
            error_code="VM_BOX_CONFIG_UNAVAILABLE",
            message="box image config is unreachable",
        ) from exc
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ServiceUnavailableError(
            error_code="VM_BOX_CONFIG_INVALID",
            message="box image config is not valid JSON",
        ) from exc
    if not isinstance(data, dict):
        raise ServiceUnavailableError(
            error_code="VM_BOX_CONFIG_INVALID",
            message="box image config has unexpected shape",
        )
    return data


def _infer_kind(name: str) -> str:
    """universal для `vm_station`-подобных боксов, иначе single."""
    return VmImageKind.UNIVERSAL if "station" in name.lower() else VmImageKind.SINGLE


def _parse_entry(name: str, value) -> dict | None:
    """Разложить запись `libvirt_box` в поля vm_images.

    Значение — либо голая URL-строка, либо объект `{url, kind?, os_versions?}`.
    Без `url` запись пропускается (None).
    """
    if isinstance(value, str):
        url = value.strip()
        if not url:
            return None
        return {"url": url, "kind": _infer_kind(name), "os_versions": []}
    if isinstance(value, dict):
        url = str(value.get("url") or "").strip()
        if not url:
            return None
        kind = value.get("kind") or _infer_kind(name)
        if kind not in (VmImageKind.UNIVERSAL, VmImageKind.SINGLE):
            kind = _infer_kind(name)
        os_versions = value.get("os_versions") or []
        if not isinstance(os_versions, list):
            os_versions = []
        return {"url": url, "kind": str(kind), "os_versions": [str(v) for v in os_versions]}
    return None


async def refresh_catalog(
    db: AsyncSession, identity: IdentityContext
) -> dict:
    """Синкнуть каталог глобальных образов с FTP-конфига. Право `vm_preset_manage`.

    Тянет `test-box-config.json`, парсит секцию `libvirt_box`, upsert'ит каждую
    запись как глобальный образ. Возвращает счётчики created/updated/synced.
    Недоступный/битый конфиг → 503.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.VM, Action.VM_PRESET_MANAGE
        )
    except AuthorizationError:
        audit_service.emit(
            "vm_image.refresh", target_type="vm_image",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    config = await asyncio.to_thread(_fetch_box_config_from_network)
    section = config.get(_LIBVIRT_BOX_SECTION) or {}
    if not isinstance(section, dict):
        section = {}
    created = 0
    updated = 0
    for name, value in section.items():
        fields = _parse_entry(str(name), value)
        if fields is None:
            continue
        was_created = await repo.sync_global(db, str(name), fields)
        if was_created:
            created += 1
        else:
            updated += 1
    await db.commit()
    synced = created + updated
    audit_service.emit(
        "vm_image.refresh", target_type="vm_image",
        status="success", allowed=True,
        details={
            "source": get_settings().vm_box_config_url,
            "synced": synced, "created": created, "updated": updated,
        },
    )
    return {
        "ok": True, "synced": synced, "created": created, "updated": updated,
        "source": get_settings().vm_box_config_url,
    }


async def list_images(
    db: AsyncSession, identity: IdentityContext, *, limit: int, offset: int
) -> tuple[list, int]:
    """Список каталога образов. Право `vm.view` (как и остальной read зоны vm)."""
    await permissions.require_action(db, identity, EntityType.VM, Action.VIEW)
    items = await repo.list_all(db, limit=limit, offset=offset)
    total = await repo.count_all(db)
    return items, total
