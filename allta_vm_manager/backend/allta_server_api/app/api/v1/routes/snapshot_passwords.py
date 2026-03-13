from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.orm import Session

from app.api.v1.crud.snapshot_passwords import (
    OSVersionNotFoundError,
    create_or_update_snapshot_password,
    delete_snapshot_password,
    get_snapshot_password,
    list_snapshot_passwords,
    update_snapshot_password,
)
from app.api.v1.dependencies import AuthVerifyResponse, require_permission
from app.api.v1.schemas.snapshot_passwords import (
    SnapshotPasswordCreate,
    SnapshotPasswordRead,
    SnapshotPasswordUpdate,
)
from app.db.session import get_db
from app.utils.config import settings

router = APIRouter(
    prefix="/passwords",
    tags=["Snapshot Passwords"],
)


@router.get(
    "/",
    summary="List snapshot passwords by OS version (requires read permission)",
    response_model=list[SnapshotPasswordRead],
)
def list_snapshot_passwords_route(
    _user: AuthVerifyResponse = Depends(
        require_permission(settings.SNAPSHOT_PASSWORDS_READ_PERMISSION)
    ),
    db: Session = Depends(get_db),
):
    return list_snapshot_passwords(db)


@router.get(
    "/{os_version_name}",
    summary="Get snapshot password by OS version name (requires read permission)",
    response_model=SnapshotPasswordRead,
)
def get_snapshot_password_route(
    os_version_name: str = Path(..., min_length=1, max_length=100),
    _user: AuthVerifyResponse = Depends(
        require_permission(settings.SNAPSHOT_PASSWORDS_READ_PERMISSION)
    ),
    db: Session = Depends(get_db),
):
    item = get_snapshot_password(db, os_version_name.strip())
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot password not found")
    return item


@router.post(
    "/",
    summary="Create or update snapshot password for OS version (requires write permission)",
    response_model=SnapshotPasswordRead,
)
def upsert_snapshot_password_route(
    payload: SnapshotPasswordCreate,
    response: Response,
    user: AuthVerifyResponse = Depends(
        require_permission(settings.SNAPSHOT_PASSWORDS_WRITE_PERMISSION)
    ),
    db: Session = Depends(get_db),
):
    normalized = SnapshotPasswordCreate(
        os_version_name=payload.os_version_name.strip(),
        ssh_username=payload.ssh_username.strip(),
        password=payload.password.strip(),
    )
    if not normalized.os_version_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="os_version_name cannot be empty")
    if not normalized.ssh_username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ssh_username cannot be empty")

    try:
        item, created = create_or_update_snapshot_password(
            db,
            normalized,
            updated_by=user.login,
        )
    except OSVersionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return item


@router.patch(
    "/{os_version_name}",
    summary="Update snapshot password for OS version (requires write permission)",
    response_model=SnapshotPasswordRead,
)
def patch_snapshot_password_route(
    payload: SnapshotPasswordUpdate,
    os_version_name: str = Path(..., min_length=1, max_length=100),
    user: AuthVerifyResponse = Depends(
        require_permission(settings.SNAPSHOT_PASSWORDS_WRITE_PERMISSION)
    ),
    db: Session = Depends(get_db),
):
    if payload.password is None:
        if payload.ssh_username is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one field is required: password, ssh_username",
            )
    normalized = SnapshotPasswordUpdate(
        password=payload.password.strip() if payload.password is not None else None,
        ssh_username=payload.ssh_username.strip() if payload.ssh_username is not None else None,
    )
    if normalized.password == "":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="password cannot be empty",
        )
    if normalized.ssh_username == "":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ssh_username cannot be empty",
        )

    try:
        item = update_snapshot_password(
            db,
            os_version_name=os_version_name.strip(),
            payload=normalized,
            updated_by=user.login,
        )
    except OSVersionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot password not found")
    return item


@router.delete(
    "/{os_version_name}",
    summary="Delete snapshot password for OS version (requires write permission)",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_snapshot_password_route(
    os_version_name: str = Path(..., min_length=1, max_length=100),
    _user: AuthVerifyResponse = Depends(
        require_permission(settings.SNAPSHOT_PASSWORDS_WRITE_PERMISSION)
    ),
    db: Session = Depends(get_db),
):
    try:
        deleted = delete_snapshot_password(db, os_version_name.strip())
    except OSVersionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot password not found")
    return
