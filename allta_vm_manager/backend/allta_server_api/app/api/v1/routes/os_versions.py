import asyncio
import logging
import os
from typing import List, Optional

import requests
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.v1.schemas.os_versions import (
    OSVersionRead,
    OSVersionSyncRead,
    OSVersionUpdate,
)
from app.api.v1.crud.os_versions import (
    get_os_version,
    get_os_versions,
    update_os_version,
    delete_os_version,
)
from app.api.v1.dependencies import get_current_admin_user
from app.db.session import SessionLocal
from app.api.v1.models.os_versions import OSVersion

log = logging.getLogger(__name__)

SYNC_INTERVAL_SEC = int(os.getenv("OS_VERSIONS_SYNC_INTERVAL", "180"))
RELEASES_URL = os.getenv(
    "RELEASES_JSON_URL",
    "http://allta.devos.astralinux.ru/rest/api/get-repo-path",
)

router = APIRouter(
    prefix="/os-versions",
    tags=["OS"],
)


@router.get(
    "/",
    response_model=List[OSVersionRead],
    summary="Список версий ОС (только управление серверами)",
    dependencies=[Depends(get_current_admin_user)],
)
def list_versions(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """
    Возвращает список всех доступных версий операционных систем.
    Доступ: только пользователи с правом управления серверами.
    """
    return get_os_versions(db, skip=skip, limit=limit)


@router.get(
    "/{version_id:int}",
    response_model=OSVersionRead,
    summary="Получить версию ОС по ID (только управление серверами)",
    dependencies=[Depends(get_current_admin_user)],
)
def read_version(
    version_id: int,
    db: Session = Depends(get_db),
):
    """
    Возвращает данные о версии ОС по её уникальному идентификатору.
    Доступ: только пользователи с правом управления серверами.
    """
    obj = get_os_version(db, version_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    return obj


@router.post(
    "/{version_id:int}",
    response_model=OSVersionRead,
    summary="Обновить существующую версию ОС (admin)",
    dependencies=[Depends(get_current_admin_user)],
)
def patch_version(
    version_id: int,
    data: OSVersionUpdate,
    db: Session = Depends(get_db),
):
    """
    Обновляет данные существующей версии ОС.  
    Доступ: только администратор.
    """
    obj = update_os_version(db, version_id, data)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    return obj


@router.delete(
    "/{version_id:int}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить версию ОС (admin)",
    dependencies=[Depends(get_current_admin_user)],
)
def delete_version(
    version_id: int,
    db: Session = Depends(get_db),
):
    """
    Удаляет версию ОС по ID.  
    Доступ: только администратор.
    """
    success = delete_os_version(db, version_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )


# -- background sync ----------------------------------------------------------

def _extract_release_names(payload: object) -> set[str]:
    if isinstance(payload, dict):
        return {str(key).strip() for key in payload.keys() if str(key).strip()}
    if isinstance(payload, list):
        names: set[str] = set()
        for item in payload:
            if isinstance(item, str) and item.strip():
                names.add(item.strip())
                continue
            if isinstance(item, dict):
                raw_name = item.get("name") or item.get("version")
                text = str(raw_name).strip() if raw_name is not None else ""
                if text:
                    names.add(text)
        return names
    raise ValueError("Unexpected releases payload type")


def _sync_versions_once() -> OSVersionSyncRead:
    """
    Тянет releases.json, добавляет новые версии в БД.
    """
    resp = requests.get(RELEASES_URL, timeout=30)
    resp.raise_for_status()
    names = _extract_release_names(resp.json())

    with SessionLocal() as db:
        existing = {row[0] for row in db.query(OSVersion.name).all()}
        new_names = sorted(names - existing)
        added = 0
        if new_names:
            for name in new_names:
                db.add(OSVersion(name=name))
            db.commit()
            added = len(new_names)

        total = db.query(OSVersion).count()
        return OSVersionSyncRead(source_url=RELEASES_URL, added=added, total=total)


@router.post(
    "/refresh",
    response_model=OSVersionSyncRead,
    summary="Обновить список версий ОС из внешнего API (admin)",
    dependencies=[Depends(get_current_admin_user)],
)
def refresh_versions():
    """
    Ручной запуск синхронизации списка версий ОС с внешним API.
    """
    try:
        return _sync_versions_once()
    except requests.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"External OS versions API is unavailable: {e}",
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Invalid response from external OS versions API: {e}",
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OS versions refresh failed: {e}",
        ) from e


async def _sync_loop():
    while True:
        try:
            added = await asyncio.to_thread(_sync_versions_once)
            if added.added:
                log.info("[os_versions] added %s new versions from releases.json", added.added)
        except Exception as e:
            log.warning("[os_versions] sync error: %s", e)
        await asyncio.sleep(SYNC_INTERVAL_SEC)


def start_os_versions_sync(app):
    if getattr(app.state, "os_versions_sync_task", None):
        return
    app.state.os_versions_sync_task = asyncio.create_task(_sync_loop())


async def stop_os_versions_sync(app):
    task: Optional[asyncio.Task] = getattr(app.state, "os_versions_sync_task", None)
    if not task:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
