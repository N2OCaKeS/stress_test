from typing import List
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status
)
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.session import get_db
from app.api.v1.schemas.os_versions import (
    OSVersionCreate,
    OSVersionRead,
    OSVersionUpdate
)
from app.api.v1.crud.os_versions import (
    get_os_version,
    get_os_versions,
    create_os_version,
    update_os_version,
    delete_os_version
)
from app.api.v1.dependencies import get_current_admin_user, get_current_user

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
    user = Depends(get_current_user),
):
    return get_os_versions(db, skip=skip, limit=limit)


@router.get(
    "/{version_id}",
    response_model=OSVersionRead,
    summary="Получить версию ОС по ID",
)
def read_version(
    version_id: int,
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
):
    obj = get_os_version(db, version_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found"
        )
    return obj

@router.post(
    "/",
    response_model=OSVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать новую версию ОС (admin)",
    dependencies=[Depends(get_current_admin_user)],
)
def create_version(
    data: OSVersionCreate,
    db: Session = Depends(get_db),
):
    try:
        return create_os_version(db, data)
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This version already exists"
        )


@router.patch(
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
    obj = update_os_version(db, version_id, data)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found"
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
    success = delete_os_version(db, version_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found"
        )
