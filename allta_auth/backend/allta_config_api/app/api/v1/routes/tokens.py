from __future__ import annotations

from typing import Any

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.crud.token_credential import (
    delete_token_credential,
    get_token_credential_by_key,
    list_token_credentials,
    update_token_credential,
    upsert_token_credential,
)
from app.api.v1.dependencies import AuthVerifyResponse, require_permission
from app.api.v1.schemas.token_credential import (
    TokenCredentialCreate,
    TokenCredentialRead,
    TokenCredentialUpdate,
)
from app.db.session import get_db
from app.utils.config import settings
from app.utils.crypto import Crypto

router = APIRouter()
_CRYPTO = Crypto()


def _decrypt_token_or_500(token_encrypted: str, *, token_key: str) -> str:
    try:
        return _CRYPTO.decrypt(token_encrypted)
    except InvalidToken:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored token for '{token_key}' cannot be decrypted",
        )


def _to_token_credential_read(item: Any) -> TokenCredentialRead:
    return TokenCredentialRead(
        id=item.id,
        token_key=item.token_key,
        token=_decrypt_token_or_500(item.token_encrypted, token_key=item.token_key),
        updated_by=item.updated_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get(
    "/tokens",
    summary="Get tokens.json content from DB (requires permission)",
    response_model=dict[str, str],
)
async def get_tokens(
    _user: AuthVerifyResponse = Depends(require_permission(settings.TOKENS_READ_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    items = await list_token_credentials(db)
    result: dict[str, str] = {}
    for item in items:
        result[item.token_key] = _decrypt_token_or_500(item.token_encrypted, token_key=item.token_key)
    return result


@router.get(
    "/tokens/details",
    summary="List token credentials (requires permission)",
    response_model=list[TokenCredentialRead],
)
async def list_token_details(
    _user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_READ_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    items = await list_token_credentials(db)
    return [_to_token_credential_read(item) for item in items]


@router.get(
    "/tokens/details/{token_key}",
    summary="Get token credential by key (requires permission)",
    response_model=TokenCredentialRead,
)
async def get_token_detail(
    token_key: str = Path(..., min_length=1, max_length=120),
    _user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_READ_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    item = await get_token_credential_by_key(db, token_key.strip())
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token credential not found")
    return _to_token_credential_read(item)


@router.post(
    "/tokens/details",
    summary="Create or update token credential (requires permission)",
    response_model=TokenCredentialRead,
)
async def upsert_token_detail(
    payload: TokenCredentialCreate,
    response: Response,
    user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_WRITE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    token_key = payload.token_key.strip()
    token = payload.token.strip()
    if not token_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token_key cannot be empty")
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token cannot be empty")

    item, created = await upsert_token_credential(
        db,
        token_key=token_key,
        token_encrypted=_CRYPTO.encrypt(token),
        updated_by=user.login,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _to_token_credential_read(item)


@router.patch(
    "/tokens/details/{token_key}",
    summary="Update token credential (requires permission)",
    response_model=TokenCredentialRead,
)
async def patch_token_detail(
    payload: TokenCredentialUpdate,
    token_key: str = Path(..., min_length=1, max_length=120),
    user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_WRITE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    token_value = payload.token.strip()
    if not token_value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token cannot be empty")

    item = await update_token_credential(
        db,
        token_key=token_key.strip(),
        token_encrypted=_CRYPTO.encrypt(token_value),
        updated_by=user.login,
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token credential not found")
    return _to_token_credential_read(item)


@router.delete(
    "/tokens/details/{token_key}",
    summary="Delete token credential (requires permission)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_token_detail(
    token_key: str = Path(..., min_length=1, max_length=120),
    _user: AuthVerifyResponse = Depends(require_permission(settings.CREDENTIALS_WRITE_PERMISSION)),
    db: AsyncSession = Depends(get_db),
):
    deleted = await delete_token_credential(db, token_key.strip())
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token credential not found")
    return

