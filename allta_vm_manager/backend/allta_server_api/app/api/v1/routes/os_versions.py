import asyncio
import logging
import os
import re
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
from app.api.v1.models.snapshot_passwords import SnapshotPassword

log = logging.getLogger(__name__)

SYNC_INTERVAL_SEC = int(os.getenv("OS_VERSIONS_SYNC_INTERVAL", "60"))
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

def _normalize_repository_urls(raw_urls: object) -> list[str]:
    items: list[str] = []

    if raw_urls is None:
        return items

    if isinstance(raw_urls, str):
        value = raw_urls.strip()
        return [value] if value else []

    if isinstance(raw_urls, dict):
        for key in ("repository_urls", "repo_urls", "repositories", "urls", "url", "path"):
            if key in raw_urls:
                return _normalize_repository_urls(raw_urls.get(key))
        return items

    if isinstance(raw_urls, (list, tuple, set)):
        for value in raw_urls:
            if isinstance(value, str):
                text = value.strip()
                if text:
                    items.append(text)

    # Keep order, remove duplicates.
    return list(dict.fromkeys(items))


def _extract_release_payload(payload: object) -> dict[str, list[str]]:
    releases: dict[str, list[str]] = {}

    if isinstance(payload, dict):
        for raw_name, raw_urls in payload.items():
            name = str(raw_name).strip()
            if not name:
                continue
            releases[name] = _normalize_repository_urls(raw_urls)
        return releases

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    releases[name] = []
                continue

            if isinstance(item, dict):
                raw_name = item.get("name") or item.get("version")
                name = str(raw_name).strip() if raw_name is not None else ""
                if not name:
                    continue
                raw_urls = (
                    item.get("repository_urls")
                    or item.get("repo_urls")
                    or item.get("repositories")
                    or item.get("urls")
                    or item.get("url")
                    or item.get("path")
                )
                releases[name] = _normalize_repository_urls(raw_urls)
        return releases

    raise ValueError("Unexpected releases payload type")


def _version_sort_key(version_name: str) -> tuple:
    """
    Ключ сортировки для версии ОС с поддержкой смешанных числовых и текстовых
    сегментов (например «1.7.6.UU.1.3»).

    Sort key for OS version that supports mixed numeric and text segments
    (e.g. "1.7.6.UU.1.3").

    Каждый сегмент представлен кортежем (тип, значение):
      (0, int)  — числовой сегмент
      (1, str)  — текстовый сегмент (UU, rc, beta и т.п.)
    Числовые сегменты всегда «меньше» текстовых на одном уровне,
    поэтому «1.7.6.11» < «1.7.6.UU.1.3» — UU-варианты идут после числовых.

    Each segment is represented as a tuple (type, value):
      (0, int)  — numeric segment
      (1, str)  — text segment (UU, rc, beta, etc.)
    Numeric segments are always "less than" text segments at the same depth,
    so "1.7.6.11" < "1.7.6.UU.1.3" — UU variants come after numeric ones.
    """
    parts = []
    for segment in re.split(r"[.\-_]", version_name):
        if segment.isdigit():
            parts.append((0, int(segment)))
        elif segment:
            parts.append((1, segment.lower()))
    return parts, version_name


def _major_version(version_name: str) -> str | None:
    numeric_parts = [int(part) for part in re.findall(r"\d+", version_name)]
    if len(numeric_parts) < 2:
        return None
    return f"{numeric_parts[0]}.{numeric_parts[1]}"


def _clone_snapshot_password_for_major(db: Session, target_os_version: OSVersion) -> bool:
    major = _major_version(str(target_os_version.name or ""))
    if not major:
        return False

    target_id = int(target_os_version.id)

    # Если пароль уже есть — ничего не делаем.
    # If password already exists — do nothing.
    exists = (
        db.query(SnapshotPassword.id)
        .filter(SnapshotPassword.os_version_id == target_id)
        .first()
    )
    if exists:
        return False

    rows = (
        db.query(SnapshotPassword, OSVersion.name)
        .join(OSVersion, SnapshotPassword.os_version_id == OSVersion.id)
        .filter(SnapshotPassword.os_version_id != target_id)
        .all()
    )

    # Шаг 1: ищем самую новую версию в том же мажоре (например 1.8).
    # Step 1: find the newest version within the same major (e.g. 1.8).
    source_password: SnapshotPassword | None = None
    source_version_name: str | None = None
    for password_row, os_version_name in rows:
        if _major_version(str(os_version_name or "")) != major:
            continue
        if source_version_name is None or _version_sort_key(str(os_version_name)) > _version_sort_key(source_version_name):
            source_password = password_row
            source_version_name = str(os_version_name)

    # Шаг 2: если в том же мажоре паролей нет (новый мажор) —
    # берём пароль от самой новой версии среди всех мажоров.
    # Step 2: if no passwords exist in the same major (new major group) —
    # take the password from the newest version across all majors.
    if source_password is None:
        for password_row, os_version_name in rows:
            if source_version_name is None or _version_sort_key(str(os_version_name)) > _version_sort_key(source_version_name):
                source_password = password_row
                source_version_name = str(os_version_name)

    if source_password is None:
        return False

    db.add(
        SnapshotPassword(
            os_version_id=target_id,
            ssh_username=source_password.ssh_username,
            password=source_password.password,  # already encrypted in DB
            updated_by=f"auto-sync:{source_version_name}",
        )
    )
    db.flush()
    return True


def _sync_versions_once() -> OSVersionSyncRead:
    """
    Тянет releases.json, добавляет новые версии в БД.
    """
    resp = requests.get(RELEASES_URL, timeout=30)
    resp.raise_for_status()
    releases = _extract_release_payload(resp.json())

    with SessionLocal() as db:
        existing_versions = db.query(OSVersion).all()
        existing_by_name = {str(item.name): item for item in existing_versions}

        # Refresh repository URLs for versions that already exist.
        for version_name, urls in releases.items():
            existing = existing_by_name.get(version_name)
            if existing is None:
                continue
            if (existing.repository_urls or []) != urls:
                existing.repository_urls = urls

        new_names = sorted(set(releases.keys()) - set(existing_by_name.keys()), key=_version_sort_key)
        added = 0
        if new_names:
            for name in new_names:
                created = OSVersion(name=name, repository_urls=releases.get(name, []))
                db.add(created)
                db.flush()  # get created.id for snapshot password binding
                _clone_snapshot_password_for_major(db, created)
            db.commit()
            added = len(new_names)
        else:
            db.commit()

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
