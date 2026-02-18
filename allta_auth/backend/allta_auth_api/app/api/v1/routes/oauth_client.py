from typing import List

from fastapi import APIRouter, Depends, HTTPException, Security, status
from sqlalchemy.orm import Session

from app.api.v1.crud.oauth_client import (
    create_oauth_client,
    delete_oauth_client,
    get_oauth_client,
    list_oauth_clients,
    update_oauth_client,
)
from app.api.v1.dependencies import get_current_admin_user
from app.api.v1.schemas.oauth_client import (
    OAuthClientCreate,
    OAuthClientRead,
    OAuthClientUpdate,
)
from app.db.session import get_db


router = APIRouter(
    prefix="/admin/oauth/clients",
    tags=["OAuth клиенты"],
)


def _to_read(item) -> OAuthClientRead:
    return OAuthClientRead(
        id=item.id,
        client_id=item.client_id,
        display_name=item.display_name,
        description=item.description,
        redirect_uri_prefixes=item.redirect_prefixes(),
        required_permission=item.required_permission,
        default_scope=item.default_scope,
        enabled=item.enabled,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get(
    "/",
    response_model=List[OAuthClientRead],
    summary="Список OAuth клиентов (admin)",
)
def admin_list_oauth_clients(
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    return [_to_read(item) for item in list_oauth_clients(db)]


@router.post(
    "/",
    response_model=OAuthClientRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать OAuth клиент (admin)",
)
def admin_create_oauth_client(
    body: OAuthClientCreate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    item = create_oauth_client(
        db,
        client_id=body.client_id,
        client_secret=body.client_secret,
        display_name=body.display_name,
        description=body.description,
        redirect_uri_prefixes=body.redirect_uri_prefixes,
        required_permission=body.required_permission,
        default_scope=body.default_scope,
        enabled=body.enabled,
    )
    return _to_read(item)


@router.patch(
    "/{oauth_client_id}",
    response_model=OAuthClientRead,
    summary="Обновить OAuth клиент (admin)",
)
def admin_update_oauth_client(
    oauth_client_id: int,
    body: OAuthClientUpdate,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    item = get_oauth_client(db, oauth_client_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OAuth client not found")

    item = update_oauth_client(
        db,
        item,
        client_secret=body.client_secret,
        display_name=body.display_name,
        description=body.description,
        redirect_uri_prefixes=body.redirect_uri_prefixes,
        required_permission=body.required_permission,
        default_scope=body.default_scope,
        enabled=body.enabled,
    )
    return _to_read(item)


@router.delete(
    "/{oauth_client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить OAuth клиент (admin)",
)
def admin_delete_oauth_client(
    oauth_client_id: int,
    db: Session = Depends(get_db),
    _admin=Security(get_current_admin_user, scopes=[]),
):
    item = get_oauth_client(db, oauth_client_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OAuth client not found")
    delete_oauth_client(db, item)
