import uuid
from typing import List, Optional
from jose import jwt
from sqlalchemy.orm import Session

from app.api.v1.models.api_token import APIToken
from app.utils.config import settings


def get_api_tokens(db: Session, user_id: int) -> List[APIToken]:
    return (
        db.query(APIToken)
        .filter(APIToken.user_id == user_id)
        .order_by(APIToken.created_at.desc())
        .all()
    )


def create_api_token(db: Session, user_id: int) -> dict:
    jti = str(uuid.uuid4())
    payload = {
        "jti": jti,
        "user_id": user_id,
    }
    token_str = jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    # сохраняем JTI в БД для отзыва
    obj = APIToken(jti=jti, user_id=user_id)
    db.add(obj)
    db.commit()
    db.refresh(obj)

    return {
        "id": obj.id,
        "token": token_str,
        "created_at": obj.created_at,
        "revoked": obj.revoked,
    }


def revoke_api_token(
    db: Session,
    token_id: int,
    user_id: Optional[int] = None,
) -> bool:
    query = db.query(APIToken).filter(APIToken.id == token_id)
    if user_id is not None:
        query = query.filter(APIToken.user_id == user_id)

    obj = query.first()
    if not obj:
        return False
    obj.revoked = True
    db.commit()
    return True


def revoke_api_token_by_jti(db: Session, jti: str) -> bool:
    obj = db.query(APIToken).filter(APIToken.jti == jti).first()
    if not obj:
        return False
    obj.revoked = True
    db.commit()
    return True

def get_api_token_by_jti(db: Session, jti: str) -> Optional[APIToken]:
    """
    Находит запись в таблице api_tokens по jti.
    """
    return db.query(APIToken).filter(APIToken.jti == jti).first()

def revoke_all_api_tokens_for_user(db: Session, user_id: int) -> int:
    """
    Удаляет (или помечает revoked=True) все API-токены для данного user_id.
    Возвращает число удалённых записей.
    """
    deleted = (
        db.query(APIToken)
        .filter(APIToken.user_id == user_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def revoke_all_api_tokens_global(db: Session) -> int:
    deleted = db.query(APIToken).delete(synchronize_session=False)
    db.commit()
    return deleted
