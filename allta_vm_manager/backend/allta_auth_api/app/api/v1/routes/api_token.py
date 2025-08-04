from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from jose import jwt

from app.api.v1.dependencies import get_current_user, get_current_admin_user
from app.api.v1.schemas.api_token import APITokenRead, APITokenCreate
from app.api.v1.crud.api_token import (
    get_api_tokens,
    create_api_token,
    revoke_api_token,
)
from app.db.session import get_db
from app.utils.config import settings

router = APIRouter(
    prefix="/users/{user_id}/tokens",
    tags=["Api key"],
)


@router.get(
    "/",
    response_model=List[APITokenRead],
    summary="Список бессрочных JWT-токенов пользователя"
)
def list_tokens(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # только админ или владелец могут смотреть токены
    if not current_user.is_admin and current_user.id != user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к токенам другого пользователя"
        )

    records = get_api_tokens(db, user_id)
    result: List[APITokenRead] = []
    for r in records:
        # r.jti — это то поле, в котором вы храните identifier для JWT
        token_str = jwt.encode(
            {"jti": r.jti, "user_id": user_id},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )
        result.append(APITokenRead(
            id=r.id,
            token=token_str,
            created_at=r.created_at,
            revoked=r.revoked,
        ))
    return result


@router.post(
    "/",
    response_model=APITokenRead,
    status_code=status.HTTP_201_CREATED,
    summary="Выдать бессрочный JWT-токен (admin)"
)
def issue_token(
    user_id: int,
    data: APITokenCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_admin_user),  # только админ
):
    # create_api_token возвращает Pydantic-схему со свежим token
    return create_api_token(db, user_id)


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать бессрочный JWT-токен (admin)"
)
def revoke_token(
    user_id: int,
    token_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_admin_user),  # только админ
):
    success = revoke_api_token(db, token_id)
    if not success:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Token not found"
        )
