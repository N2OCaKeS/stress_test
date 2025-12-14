import asyncio
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
    OSVersionUpdate,
)
from app.api.v1.crud.os_versions import (
    get_os_version,
    get_os_versions,
    update_os_version,
    delete_os_version,
)
from app.api.v1.dependencies import get_current_admin_user, get_current_user
from app.db.session import SessionLocal
from app.api.v1.models.os_versions import OSVersion

SYNC_INTERVAL_SEC = int(os.getenv("OS_VERSIONS_SYNC_INTERVAL", "900"))
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
    summary="Список версий ОС",
)
def list_versions(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Возвращает список всех доступных версий операционных систем.  
    Доступ: любой аутентифицированный пользователь.
    """
    return get_os_versions(db, skip=skip, limit=limit)


@router.get(
    "/{version_id}",
    response_model=OSVersionRead,
    summary="Получить версию ОС по ID",
)
def read_version(
    version_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Возвращает данные о версии ОС по её уникальному идентификатору.  
    Доступ: любой аутентифицированный пользователь.
    """
    obj = get_os_version(db, version_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )
    return obj


@router.post(
    "/{version_id}",
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
    "/{version_id}",
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

def _sync_versions_once() -> int:
    """
    Тянет releases.json, добавляет новые версии в БД.

    Returns:
        int: сколько новых версий было добавлено.
    """

    resp = requests.get(RELEASES_URL, timeout=30)
    resp.raise_for_status()
    releases = resp.json()
    if not isinstance(releases, dict):
        return 0
    names = {str(k) for k in releases.keys()}

    with SessionLocal() as db:
        existing = {row[0] for row in db.query(OSVersion.name).all()}
        new_names = sorted(names - existing)
        if not new_names:
            return 0
        for name in new_names:
            db.add(OSVersion(name=name))
        db.commit()
        return len(new_names)


async def _sync_loop():
    while True:
        try:
            added = await asyncio.to_thread(_sync_versions_once)
            if added:
                print(f"[os_versions] added {added} new versions from releases.json")
        except Exception as e:
            print(f"[os_versions] sync error: {e}")
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
